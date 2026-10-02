"""Trading engine shared by the back-tester and the live paper trader (one code path).

Per bar, per symbol:
  1. fill pending entry orders on this bar (latency = 1 bar)
  2. check the exit plan of open positions against this bar's path
  3. at the bar close, optionally decide a new entry -> pending order for the next bar
State is JSON-serialisable so the paper trader can persist it in the repo between runs.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from core.config import Settings
from omega.cost import CostModel
from omega.model import Calibration
from omega.signal import decide
from paper.broker import SimBroker
from risk.exits import ExitPlan, check_exit, make_exit_plan
from risk.limits import KillSwitch, correlation_ok, gross_ok
from risk.sizing import position_notional


@dataclass
class Position:
    symbol: str
    side: int
    qty: float
    entry_px: float
    entry_ts: str
    notional: float
    plan: dict
    bars_held: int = 0
    fees: float = 0.0
    funding: float = 0.0
    trust_entry: float = 0.0
    edge_bps: float = 0.0
    venue: str = ""
    maker: bool = False
    trade_id: str = ""


@dataclass
class PendingOrder:
    symbol: str
    side: int
    notional: float
    ref_px: float
    sigma_bar: float
    decision: dict
    created_ts: str


@dataclass
class EngineState:
    cash: float
    positions: dict = field(default_factory=dict)       # symbol -> Position dict
    pending: dict = field(default_factory=dict)         # symbol -> PendingOrder dict
    kill: dict = field(default_factory=dict)
    last_bar: dict = field(default_factory=dict)        # symbol -> last processed bar ts
    last_close: dict = field(default_factory=dict)      # symbol -> last close (for marking)
    n_trades: int = 0
    equity_peak: float = 0.0


class TradingEngine:
    def __init__(self, settings: Settings, calib: Calibration, seed: int = 0, state: EngineState | None = None):
        self.s = settings
        self.calib = calib
        self.cost = CostModel(settings.costs)
        self.broker = SimBroker(self.cost, seed=seed)
        self.rng = np.random.default_rng(seed + 1)
        self.state = state or EngineState(cash=settings.risk.starting_equity)
        self.kill = KillSwitch(**self.state.kill) if self.state.kill else KillSwitch()
        self.trades: list[dict] = []
        self.signals: list[dict] = []
        self.events: list[dict] = []

    # ------------------------------------------------------------------ accounting
    def equity(self) -> float:
        eq = self.state.cash
        for sym, p in self.state.positions.items():
            px = self.state.last_close.get(sym, p["entry_px"])
            eq += p["side"] * p["qty"] * (px - p["entry_px"])
        return eq

    def gross(self) -> float:
        return sum(p["qty"] * self.state.last_close.get(s, p["entry_px"]) for s, p in self.state.positions.items())

    def _event(self, ts, kind: str, **kw) -> None:
        self.events.append({"ts": str(ts), "kind": kind, **kw})

    # ------------------------------------------------------------------ main step
    def on_bar(self, symbol: str, ts: pd.Timestamp, bar: dict, pred: dict, sigma_bar: float, data_quality: float,
               bar_notional: float, funding_rate: float = 0.0, allow_entry: bool = True,
               systemic_trust: float | None = None) -> None:
        st = self.state
        self.kill.new_day(ts, self.equity())
        # 1) pending entry fill
        po = st.pending.pop(symbol, None)
        just_filled = False
        if po is not None and not self.kill.active:
            just_filled = self._fill_entry(PendingOrder(**po), ts, bar, bar_notional)
        # 2) exits
        st.last_close[symbol] = bar["close"]
        pos = st.positions.get(symbol)
        if pos is not None:
            # funding accrual (simulated perp)
            if np.isfinite(funding_rate):
                f = pos["side"] * pos["notional"] * funding_rate * self.s.bar_minutes / 480.0
                pos["funding"] += f
                st.cash -= f
            if not just_filled:
                pos["bars_held"] += 1
                reason, px = check_exit(pos["side"], ExitPlan(**pos["plan"]), bar, pos["bars_held"],
                                        pred.get("p_up"), pred.get("signal_trust"), self.kill.active)
                if reason:
                    self._close(symbol, ts, px, reason, bar_notional)
        # kill switch evaluation (after marking)
        was = self.kill.active
        if self.kill.evaluate(ts, self.equity(), self.s.risk, data_quality, systemic_trust) and not was:
            self._event(ts, "kill_switch", reason=self.kill.reason)
            for sym in list(st.positions):
                self._close(sym, ts, st.last_close.get(sym, bar["close"]), "kill_switch", bar_notional)
            st.pending.clear()
        st.last_bar[symbol] = str(ts)
        st.equity_peak = max(st.equity_peak, self.equity())
        # 3) entry decision at the close
        if allow_entry:
            self._maybe_enter(symbol, ts, bar, pred, sigma_bar, data_quality, bar_notional, funding_rate)
        st.kill = self.kill.to_dict()

    # ------------------------------------------------------------------ internals
    def _maybe_enter(self, symbol, ts, bar, pred, sigma_bar, dq, bar_notional, funding_rate) -> None:
        st, r = self.state, self.s.risk
        if symbol in st.positions or symbol in st.pending:
            return
        side = int(pred.get("side", 0) or 0)
        sigma_h_bps = sigma_bar * np.sqrt(self.s.exits.time_stop_bars) * 1e4
        eq = self.equity()
        est_notional = eq * r.risk_per_trade * 10
        cost_bps = self.cost.expected_round_trip_bps(est_notional, bar_notional, sigma_bar * 1e4,
                                                     funding_rate if np.isfinite(funding_rate) else 0.0,
                                                     side or 1, self.s.exits.time_stop_bars // 2, self.s.bar_minutes)
        d = decide(pred.get("p_up", 0.5), pred.get("meta_p", 0.0), side, sigma_h_bps,
                   pred.get("signal_trust", 0.0), cost_bps, self.calib, r.min_trust, r.kelly_fraction, dq,
                   r.min_data_quality)
        reason = d.reason
        take = d.take and not self.kill.active
        if d.take and self.kill.active:
            reason = f"kill switch active: {self.kill.reason}"
        notional = 0.0
        if take:
            stop_frac = self.s.exits.sl_mult * sigma_bar * np.sqrt(self.s.exits.time_stop_bars)
            notional = position_notional(eq, bar["close"], stop_frac, sigma_bar, d.kelly_frac, r,
                                         bars_per_year=365 * 24 * 60 / self.s.bar_minutes, size_jitter=0.15,
                                         rng=self.rng)
            open_sides = {s: p["side"] for s, p in st.positions.items()}
            if notional <= 0:
                take, reason = False, "size rounds to zero"
            elif not correlation_ok(open_sides, symbol, d.side, r):
                take, reason = False, "correlation cap"
            elif not gross_ok(self.gross(), notional, eq, r):
                take, reason = False, "gross leverage cap"
        self.signals.append({"ts": str(ts), "symbol": symbol, **d.to_dict(), "taken": bool(take),
                             "final_reason": reason, "notional": notional, "data_quality": dq})
        if take:
            st.pending[symbol] = asdict(PendingOrder(symbol, d.side, notional, bar["close"], sigma_bar,
                                                     d.to_dict(), str(ts)))

    def _fill_entry(self, po: PendingOrder, ts, bar, bar_notional) -> bool:
        f = self.broker.fill_entry(po.side, po.notional, po.ref_px, bar, bar_notional)
        qty = po.notional / f.price
        plan = make_exit_plan(po.side, f.price, po.sigma_bar, self.s.exits, self.s.risk.trust_collapse,
                              po.decision.get("edge_bps"))
        self.state.cash -= f.fee
        self.state.n_trades += 1
        tid = f"{po.symbol}-{pd.Timestamp(ts).strftime('%Y%m%d%H%M')}-{self.state.n_trades}"
        self.state.positions[po.symbol] = asdict(Position(
            po.symbol, po.side, qty, f.price, str(ts), po.notional, plan.to_dict(), 0, f.fee, 0.0,
            po.decision.get("trust", 0.0), po.decision.get("edge_bps", 0.0), f.venue, f.maker, tid))
        self._event(ts, "entry", symbol=po.symbol, side=po.side, price=f.price, notional=po.notional,
                    maker=f.maker, venue=f.venue, trade_id=tid, trust=po.decision.get("trust"),
                    edge_bps=po.decision.get("edge_bps"))
        return True

    def _close(self, symbol, ts, px, reason, bar_notional) -> None:
        st = self.state
        p = st.positions.pop(symbol, None)
        if p is None:
            return
        f = self.broker.fill_exit(p["side"], p["qty"] * px, px, reason, bar_notional)
        gross = p["side"] * p["qty"] * (f.price - p["entry_px"])
        st.cash += gross - f.fee
        net = gross - f.fee - p["fees"] - p["funding"]
        self.kill.record_trade(net)
        st.last_close[symbol] = px
        rec = {"trade_id": p["trade_id"], "symbol": symbol, "side": p["side"], "entry_ts": p["entry_ts"],
               "exit_ts": str(ts), "entry_px": p["entry_px"], "exit_px": f.price, "qty": p["qty"],
               "notional": p["notional"], "gross_pnl": gross, "fees": p["fees"] + f.fee, "funding": p["funding"],
               "net_pnl": net, "ret_bps": net / p["notional"] * 1e4, "bars_held": p["bars_held"],
               "exit_reason": reason, "trust_entry": p["trust_entry"], "edge_bps": p["edge_bps"],
               "maker_entry": p["maker"], "venue": p["venue"], "take_profit": p["plan"]["take_profit"],
               "stop_loss": p["plan"]["stop_loss"]}
        self.trades.append(rec)
        self._event(ts, "exit", symbol=symbol, reason=reason, net_pnl=net, trade_id=p["trade_id"])

    # ------------------------------------------------------------------ persistence
    def save(self, path: Path) -> None:
        self.state.kill = self.kill.to_dict()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self.state), indent=2, default=str))

    @staticmethod
    def load_state(path: Path) -> EngineState | None:
        if not path.exists():
            return None
        return EngineState(**json.loads(path.read_text()))
