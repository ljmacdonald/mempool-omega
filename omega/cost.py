"""Transaction cost model: fees + slippage (square-root impact) + latency + funding."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from core.config import CostConfig


@dataclass
class CostModel:
    cfg: CostConfig

    def fee_bps(self, maker: bool) -> float:
        return self.cfg.maker_fee_bps if maker else self.cfg.taker_fee_bps

    def slippage_bps(self, notional: float, adv_notional: float, maker: bool) -> float:
        if maker:
            return 0.0
        part = notional / max(adv_notional, 1.0)
        return self.cfg.base_slippage_bps + self.cfg.impact_coef * np.sqrt(max(part, 0.0))

    def latency_bps(self, vol_bps_per_bar: float) -> float:
        # adverse drift while the order is in flight (half a bar's stdev per latency bar)
        return 0.5 * vol_bps_per_bar * np.sqrt(self.cfg.latency_bars) * 0.2

    def funding_bps(self, funding_rate: float, side: int, holding_bars: int, bar_minutes: int) -> float:
        """Longs pay positive funding (if trading perps). Returned as a cost (can be negative)."""
        if not np.isfinite(funding_rate):
            return 0.0
        return side * funding_rate * 1e4 * holding_bars * bar_minutes / 480.0

    def expected_round_trip_bps(self, notional: float = 10_000, adv_notional: float = 5e7,
                                vol_bps_per_bar: float = 15.0, funding_rate: float = 0.0, side: int = 1,
                                holding_bars: int = 6, bar_minutes: int = 5) -> float:
        p = self.cfg.maker_fill_prob
        entry = p * self.fee_bps(True) + (1 - p) * (self.fee_bps(False) + self.slippage_bps(notional, adv_notional, False))
        exit_ = self.fee_bps(False) + self.slippage_bps(notional, adv_notional, False)  # exits assume taker
        return float(entry + exit_ + self.latency_bps(vol_bps_per_bar)
                     + max(self.funding_bps(funding_rate, side, holding_bars, bar_minutes), 0.0))
