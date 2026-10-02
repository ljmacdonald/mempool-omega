"""Decision rule:  trade only if  edge > cost + manipulation_premium(trust).

edge (bps, gross) = (meta_p * W - (1 - meta_p) * L) * sigma_h_bps
where W, L are calibrated mean win / loss sizes in sigma units.
Position fraction uses fractional Kelly on the meta probability and payoff ratio W/L.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from asi.trust import manipulation_premium_bps
from omega.model import Calibration


@dataclass
class Decision:
    side: int                 # +1 long, -1 short, 0 flat
    edge_bps: float
    cost_bps: float
    premium_bps: float
    trust: float
    meta_p: float
    p_up: float
    kelly_frac: float
    reason: str

    @property
    def take(self) -> bool:
        return self.side != 0

    def to_dict(self) -> dict:
        return asdict(self)


def decide(p_up: float, meta_p: float, side: int, sigma_h_bps: float, trust: float, cost_bps: float,
           calib: Calibration, min_trust: float, kelly_fraction: float, data_quality: float = 1.0,
           min_data_quality: float = 0.5) -> Decision:
    edge = (meta_p * calib.win_sigma - (1 - meta_p) * calib.loss_sigma) * sigma_h_bps
    prem = float(manipulation_premium_bps(trust))
    b = calib.win_sigma / max(calib.loss_sigma, 1e-9)
    kelly = max(0.0, meta_p - (1 - meta_p) / b) * kelly_fraction
    base = dict(edge_bps=float(edge), cost_bps=float(cost_bps), premium_bps=prem, trust=float(trust),
                meta_p=float(meta_p), p_up=float(p_up), kelly_frac=float(kelly))
    if side == 0 or not np.isfinite(edge):
        return Decision(0, **base, reason="no directional conviction")
    if data_quality < min_data_quality:
        return Decision(0, **base, reason=f"data quality {data_quality:.2f} < {min_data_quality}")
    if trust < min_trust:
        return Decision(0, **base, reason=f"trust {trust:.2f} < {min_trust}")
    if meta_p <= 0.5:
        return Decision(0, **base, reason=f"meta-label rejects (p={meta_p:.2f})")
    if edge <= cost_bps + prem:
        return Decision(0, **base, reason=f"edge {edge:.1f} <= cost {cost_bps:.1f} + premium {prem:.1f} bps")
    if kelly <= 0:
        return Decision(0, **base, reason="kelly <= 0")
    return Decision(int(side), **base, reason="edge clears cost + manipulation premium")
