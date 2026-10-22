"""The Perp DEX lab's one accounting module (D123). Every strategy books trades here; nothing else computes P&L.

Money is Decimal throughout. A trade has a gross price result and an itemised cost stack: a positive cost is money
paid, a negative cost is money received (funding received, a price-impact rebate). Net P&L is computed in exactly
one place, net(), as gross minus the sum of costs. Fill prices are oracle or mid prices WITHOUT impact, so impact is
charged once, as its own cost line (no double counting). Missing costs are never zero: a trade can't be booked
with a None cost.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal

D = Decimal
ZERO = D(0)
COSTS = ("open_fee", "close_fee", "price_impact", "borrow", "funding", "execution_fee", "gas", "hedge_fee",
         "hedge_slippage", "hedge_funding", "deposit_fee", "withdraw_fee", "transfer")
LABELS = {"open_fee": "Open fee", "close_fee": "Close fee", "price_impact": "Price impact", "borrow": "Borrowing",
          "funding": "Funding", "execution_fee": "Execution fee", "gas": "Network gas", "hedge_fee": "Hedge trading fees",
          "hedge_slippage": "Hedge slippage", "hedge_funding": "Hedge funding", "deposit_fee": "Pool deposit fee",
          "withdraw_fee": "Pool withdrawal fee", "transfer": "Moving money between venues"}


def d(x) -> Decimal:
    """Exact Decimal from a float, int, str or Decimal (floats go through str, so 0.1 stays 0.1)."""
    if x is None:
        raise ValueError("missing value: a cost or price can't be None")
    return x if isinstance(x, Decimal) else D(str(x))


def iso(ts: int) -> str:
    """Unix seconds -> ISO UTC time (how every trade records its times)."""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat(timespec="minutes")


def cents(x: Decimal) -> str:
    return str(d(x).quantize(D("0.01"), rounding=ROUND_HALF_EVEN))


def price_pnl(side: int, notional, entry, exit_) -> Decimal:
    """Linear perp: d x N x (exit / entry - 1). side +1 long, -1 short."""
    return D(side) * d(notional) * (d(exit_) / d(entry) - 1)


@dataclass
class Trade:
    strategy: str
    book: str                       # "account" ($100) or "reference" ($10,000)
    market: str
    side: int                       # +1 long, -1 short, 0 for pool deposits
    notional: Decimal
    opened: str
    closed: str | None = None
    gross: Decimal = ZERO           # price result before any cost
    costs: dict = field(default_factory=dict)
    est_costs: dict = field(default_factory=dict)
    reason: str = ""
    legs: list = field(default_factory=list)

    def add_cost(self, kind: str, amount) -> None:
        if kind not in COSTS:
            raise ValueError(f"unknown cost {kind}")
        self.costs[kind] = self.costs.get(kind, ZERO) + d(amount)

    @property
    def net(self) -> Decimal:
        return net(self.gross, self.costs)

    def to_json(self) -> dict:
        x = asdict(self)
        x["costs"] = {k: str(v) for k, v in self.costs.items()}
        x["est_costs"] = {k: str(v) for k, v in self.est_costs.items()}
        x["notional"], x["gross"], x["net"] = str(self.notional), str(self.gross), str(self.net)
        return x


def net(gross, costs: dict) -> Decimal:
    """THE net P&L rule: gross minus every cost (received amounts are negative costs)."""
    for k, v in costs.items():
        if v is None:
            raise ValueError(f"cost {k} is missing")
    return d(gross) - sum((d(v) for v in costs.values()), ZERO)


def waterfall(trades: list[Trade]) -> list[dict]:
    """Gross -> each cost -> net, summed over trades (the page's cost waterfall)."""
    gross = sum((t.gross for t in trades), ZERO)
    steps = [{"step": "Gross price result", "amount": str(gross)}]
    for k in COSTS:
        v = sum((t.costs.get(k, ZERO) for t in trades), ZERO)
        if v:
            steps.append({"step": LABELS[k], "amount": str(-v)})
    steps.append({"step": "Net result", "amount": str(sum((t.net for t in trades), ZERO))})
    return steps


def cost_drift(trades: list[Trade]) -> dict:
    """Estimated (at entry) vs booked costs, per cost line: how far the cost model was off."""
    est = {k: sum((d(t.est_costs.get(k, 0)) for t in trades if t.est_costs), ZERO) for k in COSTS}
    act = {k: sum((t.costs.get(k, ZERO) for t in trades if t.est_costs), ZERO) for k in COSTS}
    return {k: {"estimated": str(est[k]), "actual": str(act[k])} for k in COSTS if est[k] or act[k]}
