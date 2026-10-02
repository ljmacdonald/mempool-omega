"""Feature engine: raw bar frame -> model feature matrix (no look-ahead).

All features at row t use information available at the *close* of bar t.
Labels/returns are computed separately in ``omega.labels``.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.schema import ensure_columns
from features.calendar import event_proximity, minutes_to_funding
from features.hawkes import HawkesParams, intensity_from_counts
from features.microstructure import trade_ofi
from features.stats import log_ret, robust_z
from omega.kalman import kalman_filter


@dataclass(frozen=True)
class FeatureMeta:
    family: str            # signal family (1..10 of the brief)
    sources: tuple         # independent raw source families it depends on
    cost_to_fake: float    # prior 0..1: how expensive is it to fake this signal?
    live_only: bool = False


# Cost-to-fake priors are judgement calls documented in DECISIONS.md:
#   executed trades & liquidations cost real money (high); resting book & public
#   mempool intent are cheap to spoof (low).
FEATURES: dict[str, FeatureMeta] = {
    "ofi_trade": FeatureMeta("ofi", ("cex_primary",), 0.7),
    "ofi_trade_ema": FeatureMeta("ofi", ("cex_primary",), 0.75),
    "ofi_book": FeatureMeta("ofi", ("book",), 0.25, live_only=True),
    "depth_imbalance": FeatureMeta("depth", ("book",), 0.2, live_only=True),
    "depth_persist": FeatureMeta("depth", ("book",), 0.5, live_only=True),
    "cancel_rate": FeatureMeta("cancel_iceberg", ("book",), 0.4, live_only=True),
    "iceberg_score": FeatureMeta("cancel_iceberg", ("book", "cex_primary"), 0.6, live_only=True),
    "spread_bps": FeatureMeta("cancel_iceberg", ("book",), 0.5, live_only=True),
    "dex_intent": FeatureMeta("mempool_dex", ("eth_mempool",), 0.3),
    "gas_urgency_z": FeatureMeta("gas", ("eth_chain",), 0.6),
    "hawkes_liq_long": FeatureMeta("liq_hawkes", ("derivs",), 0.85),
    "hawkes_liq_short": FeatureMeta("liq_hawkes", ("derivs",), 0.85),
    "hawkes_liq_imb": FeatureMeta("liq_hawkes", ("derivs",), 0.85),
    "funding_z": FeatureMeta("funding_basis", ("derivs",), 0.6),
    "basis_bps": FeatureMeta("funding_basis", ("derivs", "cex_primary"), 0.6),
    "oi_chg": FeatureMeta("funding_basis", ("derivs",), 0.55),
    "oi_price_div": FeatureMeta("funding_basis", ("derivs", "cex_primary"), 0.55),
    "stable_netflow_z": FeatureMeta("stablecoin", ("eth_chain",), 0.55),
    "stable_mint_burn": FeatureMeta("stablecoin", ("eth_chain",), 0.8),
    "leadlag_cb": FeatureMeta("leadlag", ("coinbase", "cex_primary"), 0.7),
    "leadlag_kr": FeatureMeta("leadlag", ("kraken", "cex_primary"), 0.7),
    "leadlag_okx": FeatureMeta("leadlag", ("okx", "cex_primary"), 0.7),
    "venue_premium_z": FeatureMeta("leadlag", ("coinbase", "cex_primary"), 0.65),
    "leadlag_corr": FeatureMeta("leadlag", ("coinbase", "cex_primary"), 0.7),
    "xv_kalman_z": FeatureMeta("leadlag", ("coinbase", "cex_primary"), 0.7),
    "btc_fee_z": FeatureMeta("gas", ("btc_mempool",), 0.6),
    "ret_1": FeatureMeta("momentum", ("cex_primary",), 0.8),
    "ret_3": FeatureMeta("momentum", ("cex_primary",), 0.8),
    "ret_12": FeatureMeta("momentum", ("cex_primary",), 0.85),
    "rv_ratio": FeatureMeta("momentum", ("cex_primary",), 0.85),
    "volume_z": FeatureMeta("momentum", ("cex_primary",), 0.35),  # volume is cheap to wash
    "hour_sin": FeatureMeta("time_event", (), 1.0),
    "hour_cos": FeatureMeta("time_event", (), 1.0),
    "funding_proximity": FeatureMeta("time_event", (), 1.0),
    "event_proximity": FeatureMeta("time_event", (), 1.0),
}
FEATURE_NAMES = list(FEATURES)

# Liquidation Hawkes kernel used for the feature (re-fit nightly; see omega.train)
DEFAULT_LIQ_HAWKES = HawkesParams(mu=0.05, alpha=0.5, beta=0.9)


def build_features(raw: pd.DataFrame, hawkes: HawkesParams | None = None) -> pd.DataFrame:
    raw = ensure_columns(raw.copy())
    hawkes = hawkes or DEFAULT_LIQ_HAWKES
    f = pd.DataFrame(index=raw.index)
    c = raw["close"]
    r1 = log_ret(c)

    # 1. OFI
    f["ofi_trade"] = trade_ofi(raw["volume"], raw["taker_buy_volume"])
    f["ofi_trade_ema"] = f["ofi_trade"].ewm(span=6, min_periods=1).mean()
    f["ofi_book"] = raw["book_ofi"]
    # 2. Depth
    f["depth_imbalance"] = (raw["bid_depth"] - raw["ask_depth"]) / (raw["bid_depth"] + raw["ask_depth"])
    f["depth_persist"] = raw["depth_persist"]
    # 3. Cancel / iceberg
    f["cancel_rate"] = raw["cancel_rate"]
    f["iceberg_score"] = raw["iceberg_score"]
    f["spread_bps"] = raw["spread_bps"]
    # 4. Mempool DEX intent (gas-weighted)
    tot = raw["dex_buy_intent"] + raw["dex_sell_intent"]
    f["dex_intent"] = (raw["dex_buy_intent"] - raw["dex_sell_intent"]) / tot.replace(0, np.nan)
    # 9. Gas auction urgency
    f["gas_urgency_z"] = robust_z(raw["priority_fee_gwei"] / raw["base_fee_gwei"].replace(0, np.nan))
    f["btc_fee_z"] = robust_z(raw["btc_fee_fast"])
    # 5. Liquidation Hawkes
    has_liq = raw["liq_long_qty"].notna() | raw["liq_short_qty"].notna()
    ll = intensity_from_counts(np.log1p(raw["liq_long_qty"].fillna(0).to_numpy()), hawkes)
    ls = intensity_from_counts(np.log1p(raw["liq_short_qty"].fillna(0).to_numpy()), hawkes)
    # intensity_from_counts is predictable (uses < t); add current bar's events for "as of close"
    ll = ll * np.exp(-hawkes.beta) + hawkes.alpha * np.log1p(raw["liq_long_qty"].fillna(0).to_numpy())
    ls = ls * np.exp(-hawkes.beta) + hawkes.alpha * np.log1p(raw["liq_short_qty"].fillna(0).to_numpy())
    f["hawkes_liq_long"] = np.where(has_liq, ll, np.nan)
    f["hawkes_liq_short"] = np.where(has_liq, ls, np.nan)
    f["hawkes_liq_imb"] = (f["hawkes_liq_short"] - f["hawkes_liq_long"]) / (
        f["hawkes_liq_short"] + f["hawkes_liq_long"] + 1e-9)
    # 6. Funding / basis / OI stress
    f["funding_z"] = robust_z(raw["funding_rate"], window=576)
    f["basis_bps"] = (raw["mark_price"] / c - 1) * 1e4
    oi = raw["open_interest"]
    f["oi_chg"] = np.log(oi).diff(12)
    f["oi_price_div"] = f["oi_chg"] * -np.sign(log_ret(c, 12))
    # 7. Stablecoin plumbing
    f["stable_netflow_z"] = robust_z(raw["stable_exch_inflow"] - raw["stable_exch_outflow"])
    f["stable_mint_burn"] = (np.log1p(raw["stable_mint"].fillna(0)) - np.log1p(raw["stable_burn"].fillna(0))
                             ).rolling(288, min_periods=1).sum().where(raw["stable_mint"].notna()
                                                                       | raw["stable_burn"].notna())
    # 8. Cross-venue lead-lag
    for col, name in (("close_coinbase", "leadlag_cb"), ("close_kraken", "leadlag_kr"), ("close_okx", "leadlag_okx")):
        f[name] = (log_ret(raw[col]) - r1) * 1e4
    prem = raw["close_coinbase"] / c - 1
    f["venue_premium_z"] = robust_z(prem)
    f["leadlag_corr"] = log_ret(raw["close_coinbase"]).shift(1).rolling(96, min_periods=30).corr(r1)
    # Kalman fair value of primary vs Coinbase: standardised innovation (who is "ahead")
    if raw["close_coinbase"].notna().sum() > 50:
        kf = kalman_filter(np.log(c), np.log(raw["close_coinbase"]), delta=1e-4)
        f["xv_kalman_z"] = kf["innov_z"].clip(-8, 8)
    else:
        f["xv_kalman_z"] = np.nan
    # Momentum / volatility context
    f["ret_1"] = r1 * 1e4
    f["ret_3"] = log_ret(c, 3) * 1e4
    f["ret_12"] = log_ret(c, 12) * 1e4
    rv12 = r1.rolling(12, min_periods=4).std()
    rv96 = r1.rolling(96, min_periods=24).std()
    f["rv_ratio"] = rv12 / rv96
    f["volume_z"] = robust_z(np.log1p(raw["volume"]))
    # 10. Time / event
    hours = raw.index.hour + raw.index.minute / 60
    f["hour_sin"] = np.sin(2 * np.pi * hours / 24)
    f["hour_cos"] = np.cos(2 * np.pi * hours / 24)
    mtf = raw["minutes_to_funding"].fillna(pd.Series(minutes_to_funding(raw.index), index=raw.index))
    f["funding_proximity"] = np.exp(-mtf / 60.0)
    f["event_proximity"] = event_proximity(raw.index)

    f = f.replace([np.inf, -np.inf], np.nan)
    return f[FEATURE_NAMES].astype(float)


def realized_vol(raw: pd.DataFrame, window: int = 48) -> pd.Series:
    """Per-bar volatility estimate (EWMA of log returns) used for barriers, stops and sizing."""
    r = log_ret(raw["close"])
    return r.ewm(span=window, min_periods=10).std().bfill()
