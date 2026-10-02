"""Trust Score: one number in [0,1] per feature per bar.

    Trust = w1*cost_to_fake + w2*cross_source_confirm + w3*persistence
          + w4*(1 - anomaly) + w5*graph_cleanliness + w6*time_context

(weights normalised to sum to 1). Then an *integrity multiplier* applies
hard evidence of manipulation (spoof walls that vanish, wash volume, venue
outliers) that should veto a signal regardless of the weighted average.

Trade rule (see omega.signal):  edge > cost + manipulation_premium(trust)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from asi.anomaly import wash_suspicion
from features.engine import FEATURES
from features.stats import robust_z


@dataclass
class TrustWeights:
    cost_to_fake: float = 0.25
    cross_source: float = 0.25
    persistence: float = 0.15
    anomaly: float = 0.15
    graph: float = 0.10
    time_context: float = 0.10

    def normalised(self) -> np.ndarray:
        w = np.array([self.cost_to_fake, self.cross_source, self.persistence, self.anomaly, self.graph,
                      self.time_context], dtype=float)
        return w / w.sum()


# Directional "votes" from INDEPENDENT source families. Strong signals need >= 3 agreeing families.
DIRECTIONAL = {
    "cex_trades": "ofi_trade_ema",
    "book": "ofi_book",
    "eth_mempool": "dex_intent",
    "coinbase": "leadlag_cb",
    "kraken": "leadlag_kr",
    "derivs": "hawkes_liq_imb",
    "stablecoins": "stable_netflow_z",
}
FEATURE_FAMILY_VOTE = {
    "ofi_trade": "cex_trades", "ofi_trade_ema": "cex_trades", "ofi_book": "book", "depth_imbalance": "book",
    "dex_intent": "eth_mempool", "leadlag_cb": "coinbase", "venue_premium_z": "coinbase",
    "leadlag_corr": "coinbase", "leadlag_kr": "kraken", "leadlag_okx": "kraken",
    "hawkes_liq_imb": "derivs", "hawkes_liq_long": "derivs", "hawkes_liq_short": "derivs",
    "stable_netflow_z": "stablecoins", "stable_mint_burn": "stablecoins",
}
MIN_CONFIRM_SOURCES = 3


@dataclass
class TrustContext:
    graph_cleanliness: float = 0.7          # from asi.wallets on the latest transfer window
    benford: float = 0.8                    # from asi.anomaly on recent trade sizes
    row_anomaly: pd.Series | None = None    # IsolationForest score per row
    extra: dict = field(default_factory=dict)


def _votes(feat: pd.DataFrame) -> pd.DataFrame:
    v = {}
    for fam, col in DIRECTIONAL.items():
        x = feat[col] if col in feat else pd.Series(np.nan, index=feat.index)
        z = x / (x.abs().rolling(288, min_periods=20).median() + 1e-9)
        v[fam] = np.sign(z).where(z.abs() > 0.5, 0.0).where(x.notna())
    return pd.DataFrame(v, index=feat.index)


def cross_source_confirmation(feat: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Per-feature agreement with OTHER independent families; plus consensus direction in [-1,1]."""
    votes = _votes(feat)
    n_avail = votes.notna().sum(axis=1)
    consensus = votes.sum(axis=1) / n_avail.replace(0, np.nan)
    out = {}
    for f in feat.columns:
        fam = FEATURE_FAMILY_VOTE.get(f)
        if fam is None:
            # non-directional context features: confirmation = breadth of independent data available
            out[f] = np.clip(n_avail / MIN_CONFIRM_SOURCES, 0, 1) * 0.8
            continue
        own = votes[fam]
        others = votes.drop(columns=[fam])
        n_o = others.notna().sum(axis=1)
        agree = (others.mul(own, axis=0) > 0).sum(axis=1)
        frac = agree / n_o.replace(0, np.nan)
        breadth = np.clip((n_o + 1) / MIN_CONFIRM_SOURCES, 0, 1)
        out[f] = (frac.fillna(0) * breadth).where(own != 0, 0.3 * breadth)
    return pd.DataFrame(out, index=feat.index).clip(0, 1), consensus.fillna(0)


def persistence(feat: pd.DataFrame, k: int = 6) -> pd.DataFrame:
    sgn = np.sign(feat)
    same = sum((sgn == sgn.shift(i)).astype(float) for i in range(1, k + 1)) / k
    return same.where(feat.notna())


def anomaly_component(feat: pd.DataFrame, row_anomaly: pd.Series | None) -> pd.DataFrame:
    """Extreme values vs own history are suspicious (bait signals tend to be outsized)."""
    out = {}
    for f in feat.columns:
        z = robust_z(feat[f], window=288, min_periods=30).abs()
        out[f] = ((z - 3) / 5).clip(0, 1).fillna(0)
    a = pd.DataFrame(out, index=feat.index)
    if row_anomaly is not None:
        a = a.combine(pd.DataFrame({c: row_anomaly.reindex(feat.index).fillna(0) for c in feat.columns}),
                      np.maximum)
    return a


def time_context(feat: pd.DataFrame) -> pd.DataFrame:
    """Lower trust where manipulation is cheap/likely: around funding prints (funding/basis games),
    during liquidation cascades (liquidation hunting) and thin weekend books."""
    idx = feat.index
    near_funding = feat.get("funding_proximity", pd.Series(0.0, index=idx)).fillna(0)
    cascade = (feat.get("hawkes_liq_long", pd.Series(0.0, index=idx)).fillna(0)
               + feat.get("hawkes_liq_short", pd.Series(0.0, index=idx)).fillna(0))
    cascade = np.clip(cascade / 6.0, 0, 1)
    weekend = pd.Series(idx.dayofweek >= 5, index=idx).astype(float)
    out = {}
    for f, m in FEATURES.items():
        if f not in feat:
            continue
        t = pd.Series(1.0, index=idx)
        if m.family == "funding_basis":
            t -= 0.5 * near_funding
        if m.family in ("ofi", "depth", "cancel_iceberg", "momentum", "leadlag"):
            t -= 0.4 * cascade
        if m.family in ("depth", "cancel_iceberg"):
            t -= 0.2 * weekend
        out[f] = t.clip(0, 1)
    return pd.DataFrame(out, index=idx)


def integrity_multiplier(feat: pd.DataFrame, ctx: TrustContext, raw: pd.DataFrame | None = None) -> pd.DataFrame:
    """Hard vetoes from direct manipulation evidence."""
    idx = feat.index
    mult = pd.DataFrame(1.0, index=idx, columns=feat.columns)
    # Spoofing: book signals with vanishing depth / heavy cancels
    dp = feat.get("depth_persist", pd.Series(np.nan, index=idx))
    cr = feat.get("cancel_rate", pd.Series(np.nan, index=idx))
    spoof = (1 - dp.fillna(0.7)).clip(0, 1) * 0.6 + cr.fillna(0.3).clip(0, 1) * 0.4
    spoof_mult = (1 - ((spoof - 0.35) / 0.4).clip(0, 1)).clip(0.05, 1)
    for f in ("ofi_book", "depth_imbalance"):
        if f in mult:
            mult[f] *= spoof_mult
    # Wash trading: volume without impact; plus Benford conformity of recent trade sizes
    rv = feat.get("ret_1", pd.Series(0.0, index=idx)).abs().rolling(96, min_periods=10).median()
    wash = wash_suspicion(feat.get("volume_z", pd.Series(0.0, index=idx)), feat.get("ret_1", pd.Series(0.0, index=idx)),
                          rv)
    for f in ("ofi_trade", "ofi_trade_ema", "volume_z"):
        if f in mult:
            mult[f] *= (1 - 0.9 * wash) * (0.5 + 0.5 * ctx.benford)
    # Lead-lag spoof: one venue jumps without the others
    if {"leadlag_cb", "leadlag_kr", "leadlag_okx"} <= set(feat.columns):
        cb, others = feat["leadlag_cb"], feat[["leadlag_kr", "leadlag_okx"]].abs().max(axis=1)
        lone = ((cb.abs() - 3 * others.fillna(0) - 5) / 20).clip(0, 1).fillna(0)
        for f in ("leadlag_cb", "venue_premium_z", "leadlag_corr"):
            mult[f] *= (1 - 0.9 * lone)
    # Mempool poisoning: huge DEX intent with no gas urgency behind it (cheap spam)
    if "dex_intent" in feat:
        gz = feat.get("gas_urgency_z", pd.Series(0.0, index=idx)).fillna(0)
        spam = ((feat["dex_intent"].abs() - 0.6) / 0.4).clip(0, 1).fillna(0) * (gz < 0.5)
        mult["dex_intent"] *= (1 - 0.8 * spam)
    return mult.clip(0, 1)


def compute_trust(feat: pd.DataFrame, ctx: TrustContext | None = None, weights: TrustWeights | None = None,
                  raw: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict]:
    """Return (trust per feature per row, diagnostics)."""
    ctx = ctx or TrustContext()
    w = (weights or TrustWeights()).normalised()
    cols = list(feat.columns)
    ctf = pd.DataFrame({f: FEATURES[f].cost_to_fake if f in FEATURES else 0.5 for f in cols}, index=feat.index)
    xs, consensus = cross_source_confirmation(feat)
    pers = persistence(feat).fillna(0.5)
    anom = anomaly_component(feat, ctx.row_anomaly)
    graph = pd.DataFrame(1.0, index=feat.index, columns=cols)
    for f in ("stable_netflow_z", "stable_mint_burn", "dex_intent"):
        if f in graph:
            graph[f] = ctx.graph_cleanliness
    tctx = time_context(feat).reindex(columns=cols).fillna(1.0)
    trust = (w[0] * ctf + w[1] * xs.reindex(columns=cols) + w[2] * pers + w[3] * (1 - anom)
             + w[4] * graph + w[5] * tctx)
    trust = (trust * integrity_multiplier(feat, ctx, raw)).clip(0, 1)
    trust = trust.where(feat.notna())  # no data -> no trust (NaN)
    diag = {"consensus": consensus}
    return trust, diag


def trust_gate(trust: pd.DataFrame, lo: float = 0.25, hi: float = 0.60) -> pd.DataFrame:
    """Map feature trust -> weight on that feature's model contribution (trust-weighted inference).
    trust >= hi keeps the full contribution, <= lo neutralises it. Missing features (NaN trust) keep
    their (missing-value) contribution: absence of data is itself information the model learned."""
    return ((trust - lo) / (hi - lo)).clip(0, 1).fillna(1.0)


def signal_trust(trust: pd.DataFrame, contrib: pd.DataFrame, side: pd.Series | None = None) -> pd.Series:
    """Trust of a decision = trust of the features that PUSH in the decision's direction, weighted by how
    hard they push. Features that oppose the trade, or are missing, do not count."""
    c = contrib.reindex(columns=trust.columns).fillna(0)
    if side is None:
        side = np.sign(c.sum(axis=1))
    w = c.mul(side, axis=0).clip(lower=0).where(trust.notna(), 0.0)
    denom = w.sum(axis=1).replace(0, np.nan)
    return ((w * trust.fillna(0)).sum(axis=1) / denom).fillna(0.0).clip(0, 1)


def manipulation_premium_bps(trust: float | np.ndarray | pd.Series, max_premium_bps: float = 40.0,
                             floor_bps: float = 1.0):
    """Extra edge (bps) demanded because the signal might be bait. Convex in (1 - trust)."""
    t = np.clip(trust, 0, 1)
    return floor_bps + max_premium_bps * (1 - t) ** 2
