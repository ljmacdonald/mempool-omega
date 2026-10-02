"""Assemble the canonical per-symbol bar frame from all free sources.

``load_frame(symbol, n_bars)`` is the single entry point used by training,
back-testing and the paper trader. It returns ``(frame, health)`` where
``health`` maps source-family -> {ok, latency_s, error, staleness_s}. The ASI
layer turns ``health`` into data-quality / cross-source scores.

Historical depth/mempool data is not freely available, so live-only columns
are populated for the most recent bar only (NaN historically). The model is
trained with NaN in those columns and learns to use them when present; this
is a known limitation documented in DECISIONS.md.
"""
from __future__ import annotations

import time
from collections.abc import Callable

import numpy as np
import pandas as pd

from core.config import get_settings
from core.log import get_logger
from core.schema import ensure_columns
from core.synthetic import generate_anchored
from ingest import cex, onchain
from ingest.http import SourceUnavailable

log = get_logger("ingest.history")


_SHARED_CACHE: dict[str, tuple[float, object]] = {}
SHARED_TTL_S = 120.0
# Sources that are not symbol-specific: fetch once per run, reuse for every symbol.
SHARED_SOURCES = {"eth_chain", "eth_mempool", "stablecoins", "btc_mempool"}


def _timed(health: dict, name: str, fn: Callable, *a, **kw):
    if name in SHARED_SOURCES:
        hit = _SHARED_CACHE.get(name)
        if hit and time.monotonic() - hit[0] < SHARED_TTL_S:
            health[name] = {"ok": hit[1] is not None, "latency_s": 0.0, "note": "cached"}
            return hit[1]
    out = _timed_raw(health, name, fn, *a, **kw)
    if name in SHARED_SOURCES:
        _SHARED_CACHE[name] = (time.monotonic(), out)
    return out


def _timed_raw(health: dict, name: str, fn: Callable, *a, **kw):
    t0 = time.monotonic()
    try:
        out = fn(*a, **kw)
        health[name] = {"ok": True, "latency_s": round(time.monotonic() - t0, 3)}
        return out
    except Exception as e:  # noqa: BLE001
        health[name] = {"ok": False, "latency_s": round(time.monotonic() - t0, 3), "error": str(e)[:200]}
        log.warning("source %s unavailable: %s", name, str(e)[:160])
        return None


def _closed_only(df: pd.DataFrame, bar_minutes: int) -> pd.DataFrame:
    now = pd.Timestamp.now(tz="UTC")
    return df[df.index + pd.Timedelta(minutes=bar_minutes) <= now]


def _bin_events(times: pd.DatetimeIndex, values: np.ndarray, index: pd.DatetimeIndex, freq: str) -> pd.Series:
    if len(times) == 0:
        return pd.Series(0.0, index=index)
    s = pd.Series(values, index=times.floor(freq)).groupby(level=0).sum()
    return s.reindex(index).fillna(0.0)


def load_frame(symbol: str, n_bars: int | None = None, mode: str | None = None,
               capture_seconds: float = 0.0, live_extras: bool = True) -> tuple[pd.DataFrame, dict]:
    s = get_settings()
    n_bars = n_bars or s.history_bars
    mode = mode or s.data_mode
    if mode == "synthetic":
        df = generate_anchored(n_bars, s.bar_minutes, s.seed, symbol)
        return df, {"synthetic": {"ok": True, "latency_s": 0.0}}

    health: dict = {}
    freq = s.pandas_freq
    prim = _timed(health, "cex_primary", cex.binance_klines, symbol, s.bar, n_bars)
    if prim is None:
        okx_spot = _timed(health, "cex_primary_okx", cex.okx_candles, symbol, s.bar, n_bars)
        if okx_spot is not None:
            prim = okx_spot.assign(taker_buy_volume=np.nan, n_trades=np.nan)
            health["cex_primary"] = {"ok": True, "latency_s": health["cex_primary_okx"]["latency_s"],
                                     "note": "okx fallback"}
    if prim is None:
        if mode == "live":
            raise SourceUnavailable(f"no primary market data for {symbol}")
        log.warning("falling back to SYNTHETIC data for %s (auto mode)", symbol)
        df = generate_anchored(n_bars, s.bar_minutes, s.seed, symbol)
        health["synthetic"] = {"ok": True, "latency_s": 0.0, "note": "fallback"}
        return df, health

    df = _closed_only(prim, s.bar_minutes).iloc[-n_bars:].copy()
    idx = df.index

    # Cross-venue closes
    cb = _timed(health, "coinbase", cex.coinbase_candles, symbol, s.bar_minutes * 60, min(n_bars, 1500))
    if cb is not None:
        df["close_coinbase"] = cb["close"].reindex(idx)
    kr = _timed(health, "kraken", cex.kraken_ohlc, symbol, s.bar_minutes)
    if kr is not None:
        df["close_kraken"] = kr["close"].reindex(idx)
    ok = _timed(health, "okx", cex.okx_candles, symbol, s.bar, min(n_bars, 1440))
    if ok is not None:
        df["close_okx"] = ok["close"].reindex(idx)

    # Derivatives (OKX perp; Binance futures is geo-blocked from most clouds)
    perp = _timed(health, "derivs", cex.okx_candles, symbol, s.bar, min(n_bars, 1440), True)
    if perp is not None:
        df["mark_price"] = perp["close"].reindex(idx)
    fh = _timed(health, "derivs_funding", cex.okx_funding_history, symbol)
    if fh is not None and len(fh):
        df["funding_rate"] = fh.reindex(idx.union(fh.index)).ffill().reindex(idx)
    oi = _timed(health, "derivs_oi", cex.okx_open_interest_history, symbol, s.bar)
    if oi is not None and len(oi):
        df["open_interest"] = oi.reindex(idx)
    liq = _timed(health, "derivs_liq", cex.okx_liquidations, symbol)
    if liq is not None and len(liq):
        first = liq.index.min().floor(freq)
        covered = idx >= first
        ll = _bin_events(liq.index[liq["pos_side"] == "long"], liq.loc[liq["pos_side"] == "long", "sz"].to_numpy(),
                         idx, freq)
        ls = _bin_events(liq.index[liq["pos_side"] == "short"],
                         liq.loc[liq["pos_side"] == "short", "sz"].to_numpy(), idx, freq)
        df["liq_long_qty"] = ll.where(covered)
        df["liq_short_qty"] = ls.where(covered)
        df["liq_count"] = (ll + ls).where(covered)

    if live_extras and len(df):
        last = idx[-1]
        fr = _timed(health, "derivs_now", cex.okx_funding, symbol)
        if fr is not None:
            df.loc[last, "funding_rate"] = fr["funding_rate"]
            mins = (fr["next_funding_ms"] / 1000 - time.time()) / 60
            df.loc[last, "minutes_to_funding"] = max(mins, 0.0)
        g = _timed(health, "eth_chain", onchain.eth_gas_urgency)
        if g is not None:
            df.loc[last, "base_fee_gwei"] = g["base_fee_gwei"]
            df.loc[last, "priority_fee_gwei"] = g["priority_fee_gwei"]
        di = _timed(health, "eth_mempool", onchain.eth_dex_intent)
        if di is not None:
            df.loc[last, "dex_buy_intent"] = di.buy
            df.loc[last, "dex_sell_intent"] = di.sell
        sf = _timed(health, "stablecoins", onchain.stablecoin_flows)
        if sf is not None:
            for k in ("stable_mint", "stable_burn", "stable_exch_inflow", "stable_exch_outflow"):
                df.loc[last, k] = sf[k]
            df.attrs["transfers"] = list(sf["transfers"].itertuples(index=False, name=None))
        bm = _timed(health, "btc_mempool", onchain.btc_mempool_stats)
        if bm is not None:
            df.loc[last, "btc_mempool_vsize"] = bm["btc_mempool_vsize"]
            df.loc[last, "btc_fee_fast"] = bm["btc_fee_fast"]
        book_feats = _live_book(symbol, capture_seconds, health)
        for k, v in book_feats.items():
            df.loc[last, k] = v

    df = ensure_columns(df)
    df.attrs["data_mode"] = "live"
    df.attrs["symbol"] = symbol
    return df, health


def _live_book(symbol: str, capture_seconds: float, health: dict) -> dict:
    """Book microstructure from a time-boxed WS capture, else a short REST polling window."""
    from features.microstructure import book_features

    if capture_seconds > 0:
        from ingest.ws import capture_any

        cap = _timed(health, "book", capture_any, symbol, capture_seconds)
        if cap and cap.get("books"):
            return book_features(cap["books"], cap.get("trades"))
    books = []
    for _ in range(6):
        b = _timed(health, "book", cex.okx_books, symbol)
        if b is None:
            break
        b["ts"] = b["ts"].timestamp()
        books.append(b)
        time.sleep(0.5)
    return book_features(books, []) if books else {}


def load_universe(symbols: list[str] | None = None, **kw) -> tuple[dict[str, pd.DataFrame], dict[str, dict]]:
    s = get_settings()
    frames, healths = {}, {}
    for sym in symbols or s.symbols:
        frames[sym], healths[sym] = load_frame(sym, **kw)
    return frames, healths
