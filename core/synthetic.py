"""Synthetic market generator.

Used for: offline demos, CI tests, red-team simulations and as a graceful
fallback when public endpoints are unreachable.

IMPORTANT (honesty): the synthetic market contains *planted*, weak, noisy alpha
(order-flow, lead-lag, liquidation and stablecoin effects) so that the pipeline
has something to find. Performance on synthetic data says NOTHING about real
markets. Every artefact produced from synthetic data is tagged ``data_mode=synthetic``.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.schema import ensure_columns


@dataclass
class SyntheticSpec:
    n_bars: int = 4000
    bar_minutes: int = 5
    start_price: float = 60_000.0
    vol_per_bar: float = 0.0018       # ~0.18% per 5m bar
    alpha_strength: float = 0.07      # fraction of next-bar return explained by latent signal
    seed: int = 7
    end: pd.Timestamp | None = None


ANCHOR = pd.Timestamp("2026-01-01", tz="UTC")


def generate_anchored(n_bars: int, bar_minutes: int = 5, seed: int = 7, symbol: str = "BTCUSDT",
                      now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Synthetic history that is STABLE across runs: always generated from a fixed anchor date, so the
    bar at a given timestamp has the same values every hourly paper run (needed for consistent state)."""
    now = now or pd.Timestamp.now(tz="UTC")
    end = now.floor(f"{bar_minutes}min") - pd.Timedelta(minutes=bar_minutes)  # last CLOSED bar
    total = max(int((end - ANCHOR) / pd.Timedelta(minutes=bar_minutes)) + 1, n_bars)
    df = generate_market(SyntheticSpec(n_bars=total, bar_minutes=bar_minutes, seed=seed, end=end), symbol)
    return df.iloc[-n_bars:]


def _hawkes_events(n: int, mu: float, alpha: float, beta: float, rng: np.random.Generator) -> np.ndarray:
    """Discrete-time Hawkes counts per bar (Ogata-style thinning approximated per bar)."""
    counts = np.zeros(n, dtype=float)
    lam_excess = 0.0
    decay = np.exp(-beta)
    for t in range(n):
        lam = mu + lam_excess
        k = rng.poisson(lam)
        counts[t] = k
        lam_excess = lam_excess * decay + alpha * k
    return counts


def generate_market(spec: SyntheticSpec | None = None, symbol: str = "BTCUSDT") -> pd.DataFrame:
    spec = spec or SyntheticSpec()
    if symbol.startswith("ETH") and spec.start_price == 60_000.0:
        spec = SyntheticSpec(**{**spec.__dict__, "start_price": 3_000.0})
    rng = np.random.default_rng(spec.seed + (zlib.crc32(symbol.encode()) % 1000 if symbol != "BTCUSDT" else 0))
    n = spec.n_bars
    end = spec.end or pd.Timestamp.now(tz="UTC").floor(f"{spec.bar_minutes}min")
    idx = pd.date_range(end=end, periods=n, freq=f"{spec.bar_minutes}min", tz="UTC")

    # Latent order-flow state (AR(1)) -> drives next-bar returns
    s = np.zeros(n)
    phi = 0.85
    eps = rng.normal(size=n)
    for t in range(1, n):
        s[t] = phi * s[t - 1] + np.sqrt(1 - phi**2) * eps[t]

    # Regime-switching volatility
    regime = (np.sin(np.arange(n) / 400.0) > 0.6).astype(float)
    sigma = spec.vol_per_bar * (1.0 + 1.2 * regime)

    # Liquidation cascades: Hawkes on both sides
    liq_long = _hawkes_events(n, mu=0.05, alpha=0.55, beta=0.9, rng=rng)
    liq_short = _hawkes_events(n, mu=0.05, alpha=0.55, beta=0.9, rng=rng)

    # Stablecoin plumbing: inflows to exchanges precede buying (weak)
    stable_in = rng.gamma(2.0, 1.0, n) * 1e6
    stable_out = rng.gamma(2.0, 1.0, n) * 1e6
    stable_in *= np.exp(0.10 * s)

    a = spec.alpha_strength
    noise = rng.normal(size=n)
    ret = np.zeros(n)
    liq_pressure = (liq_short - liq_long) / 5.0
    ret[1:] = sigma[1:] * (a * s[:-1] + 0.05 * np.tanh(liq_pressure[:-1]) + np.sqrt(max(1 - a**2, 0.05)) * noise[1:])
    close = spec.start_price * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]]
    wick = np.abs(rng.normal(0, 0.6, n)) * sigma * close
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.6, n)) * sigma * close
    volume = rng.gamma(3.0, 40.0, n) * (1 + 0.8 * regime) * (1 + 0.3 * (liq_long + liq_short))
    buy_frac = np.clip(0.5 + 0.12 * np.tanh(s + 0.7 * rng.normal(size=n)), 0.02, 0.98)

    df = pd.DataFrame(index=idx)
    df.index.name = "ts"
    df["open"], df["high"], df["low"], df["close"] = open_, high, low, close
    df["volume"] = volume
    df["taker_buy_volume"] = volume * buy_frac
    df["n_trades"] = (volume * rng.uniform(8, 12, n)).round()

    # Cross-venue: coinbase LEADS by one bar part of the time; kraken lags; okx ~ same
    lead_ret = np.r_[ret[1:], 0.0]
    # Coinbase sometimes already "contains" a sliver of the next primary-venue move (bounded divergence)
    df["close_coinbase"] = close * np.exp(0.06 * lead_ret + rng.normal(0, 2e-4, n))
    df["close_kraken"] = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 1e-4, n))
    df["close_okx"] = close * (1 + rng.normal(0, 5e-5, n))

    # Derivatives
    funding = 0.0001 + 0.00015 * np.tanh(pd.Series(s).rolling(48, min_periods=1).mean().to_numpy())
    df["funding_rate"] = funding + rng.normal(0, 2e-5, n)
    df["mark_price"] = close * (1 + funding * 2 + rng.normal(0, 1e-4, n))
    oi = 5e9 * np.exp(np.cumsum(rng.normal(0, 0.002, n) + 0.001 * s))
    df["open_interest"] = oi
    df["liq_long_qty"] = liq_long * rng.uniform(0.5, 2.0, n)
    df["liq_short_qty"] = liq_short * rng.uniform(0.5, 2.0, n)
    df["liq_count"] = liq_long + liq_short
    minute_of_day = idx.hour * 60 + idx.minute
    df["minutes_to_funding"] = (480 - (minute_of_day % 480)) % 480

    # Order book (normally live-only)
    bid = rng.lognormal(3.0, 0.3, n) * (1 + 0.25 * np.tanh(s))
    ask = rng.lognormal(3.0, 0.3, n) * (1 - 0.25 * np.tanh(s))
    df["bid_depth"], df["ask_depth"] = bid, ask
    df["book_ofi"] = np.tanh(s + rng.normal(0, 0.8, n))
    df["depth_persist"] = np.clip(0.7 + 0.1 * rng.normal(size=n), 0, 1)
    df["cancel_rate"] = np.clip(0.3 + 0.1 * rng.normal(size=n), 0, 1)
    df["iceberg_score"] = np.clip(rng.beta(1.5, 8, n), 0, 1)
    df["spread_bps"] = np.clip(1.0 + 0.5 * regime + rng.gamma(1.0, 0.3, n), 0.1, None)

    # On-chain
    gas_base = 8 + 6 * regime + rng.gamma(2, 2, n)
    dex_net = np.tanh(0.6 * s + rng.normal(0, 1.0, n))
    df["dex_buy_intent"] = (1 + dex_net) * gas_base * 10
    df["dex_sell_intent"] = (1 - dex_net) * gas_base * 10
    df["base_fee_gwei"] = gas_base
    df["priority_fee_gwei"] = rng.gamma(1.5, 0.8, n) * (1 + 0.5 * (liq_long + liq_short > 3))
    df["stable_mint"] = rng.binomial(1, 0.02, n) * rng.uniform(5e7, 2e8, n)
    df["stable_burn"] = rng.binomial(1, 0.02, n) * rng.uniform(5e7, 2e8, n)
    df["stable_exch_inflow"] = stable_in
    df["stable_exch_outflow"] = stable_out
    df["btc_mempool_vsize"] = 5e7 * (1 + 0.5 * regime) * rng.uniform(0.8, 1.2, n)
    df["btc_fee_fast"] = 5 + 10 * regime + rng.gamma(2, 1.5, n)
    df.attrs["data_mode"] = "synthetic"
    df.attrs["symbol"] = symbol
    return ensure_columns(df)


def synthetic_trade_sizes(n: int = 2000, seed: int = 0, wash: bool = False) -> np.ndarray:
    """Organic trade sizes are ~log-normal (Benford-compliant); wash trades cluster on round sizes."""
    rng = np.random.default_rng(seed)
    sizes = rng.lognormal(-2.0, 1.6, n)
    if wash:
        k = n // 2
        sizes[:k] = rng.choice([0.1, 0.5, 1.0, 5.0, 10.0], size=k)
    return sizes


def synthetic_transfers(n: int = 300, seed: int = 0, wash_ring: bool = False) -> pd.DataFrame:
    """Random ERC-20 style transfers among wallets; optional wash ring (A->B->C->A)."""
    rng = np.random.default_rng(seed)
    wallets = [f"0x{rng.integers(0, 2**63):016x}" for _ in range(80)]
    rows = []
    for _ in range(n):
        a, b = rng.choice(wallets, 2, replace=False)
        rows.append((a, b, float(rng.lognormal(10, 1.5))))
    if wash_ring:
        ring = wallets[:4]
        for _ in range(30):
            for i in range(len(ring)):
                rows.append((ring[i], ring[(i + 1) % len(ring)], 1_000_000.0))
    return pd.DataFrame(rows, columns=["from", "to", "value"])
