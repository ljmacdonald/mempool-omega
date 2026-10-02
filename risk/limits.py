"""Portfolio limits & the kill switch."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

from core.config import RiskConfig


@dataclass
class KillSwitch:
    active: bool = False
    reason: str = ""
    since: str = ""
    day: str = ""
    day_start_equity: float = 0.0
    consecutive_losses: int = 0
    history: list = field(default_factory=list)

    def new_day(self, ts: pd.Timestamp, equity: float) -> None:
        d = ts.strftime("%Y-%m-%d")
        if d != self.day:
            self.day = d
            self.day_start_equity = equity
            # Drawdown and losing-streak halts cool off until the next UTC day, then re-arm.
            if self.active and (self.reason.startswith("daily_drawdown") or self.reason.startswith("consecutive")):
                self.active, self.reason = False, ""
                self.consecutive_losses = 0

    def record_trade(self, pnl: float) -> None:
        self.consecutive_losses = self.consecutive_losses + 1 if pnl < 0 else 0

    def evaluate(self, ts: pd.Timestamp, equity: float, cfg: RiskConfig, data_quality: float,
                 trust: float | None = None) -> bool:
        reasons = []
        if self.day_start_equity > 0 and equity / self.day_start_equity - 1 <= -cfg.daily_dd_kill:
            reasons.append(f"daily_drawdown {equity / self.day_start_equity - 1:.2%}")
        if self.consecutive_losses >= cfg.max_consecutive_losses:
            reasons.append(f"consecutive_losses {self.consecutive_losses}")
        if data_quality < cfg.min_data_quality * 0.5:
            reasons.append(f"data_failure quality={data_quality:.2f}")
        if trust is not None and trust < cfg.trust_collapse * 0.5:
            reasons.append(f"systemic_trust_collapse {trust:.2f}")
        if reasons and not self.active:
            self.active, self.reason, self.since = True, "; ".join(reasons), str(ts)
            self.history.append({"ts": str(ts), "reason": self.reason})
        elif self.active and not reasons and not (self.reason.startswith("daily_drawdown")
                                                  or self.reason.startswith("consecutive")):
            # data / trust halts auto re-arm once conditions clear; P&L halts wait for the next UTC day
            self.active, self.reason = False, ""
        return self.active

    def to_dict(self) -> dict:
        return asdict(self)


def correlation_ok(open_sides: dict[str, int], new_symbol: str, new_side: int, cfg: RiskConfig) -> bool:
    """Crypto majors are highly correlated: cap the number of same-direction positions."""
    same = sum(1 for s, sd in open_sides.items() if s != new_symbol and sd == new_side)
    return same < cfg.max_correlated_positions


def gross_ok(gross_notional: float, add_notional: float, equity: float, cfg: RiskConfig) -> bool:
    return (gross_notional + add_notional) <= cfg.max_gross_leverage * equity
