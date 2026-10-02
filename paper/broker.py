"""Simulated execution: maker-first with taker fallback, randomised size/timing/venue/order type.

No real orders are ever sent from this module.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from omega.cost import CostModel


@dataclass
class Fill:
    price: float
    fee: float
    maker: bool
    venue: str
    slippage_bps: float
    order_type: str


class SimBroker:
    def __init__(self, cost: CostModel, seed: int = 0, venues: tuple[str, ...] = ("binance", "okx", "coinbase"),
                 taker_randomization: float = 0.1):
        self.cost = cost
        self.rng = np.random.default_rng(seed)
        self.venues = venues
        self.taker_randomization = taker_randomization

    def fill_entry(self, side: int, notional: float, ref_price: float, bar: dict, bar_notional: float) -> Fill:
        """Maker-first: a limit at ref_price (last close) fills if the next bar trades through it and
        we win the queue (prob ``maker_fill_prob``); else cancel/replace as taker at the bar open + slippage.
        A small share of orders go straight to taker so our footprint is less predictable."""
        venue = str(self.rng.choice(self.venues))
        cfg = self.cost.cfg
        go_taker = self.rng.random() < self.taker_randomization
        touched = bar["low"] < ref_price if side > 0 else bar["high"] > ref_price
        if not go_taker and touched and self.rng.random() < cfg.maker_fill_prob:
            fee = notional * self.cost.fee_bps(True) / 1e4
            return Fill(ref_price, fee, True, venue, 0.0, "limit_post_only")
        slip = self.cost.slippage_bps(notional, bar_notional, maker=False)
        px = bar["open"] * (1 + side * slip / 1e4)
        fee = notional * self.cost.fee_bps(False) / 1e4
        return Fill(px, fee, False, venue, slip, "market" if go_taker else "limit->market")

    def fill_exit(self, side: int, notional: float, price: float, reason: str, bar_notional: float) -> Fill:
        """Profit targets rest as limits (maker); everything else exits aggressively (taker + slippage)."""
        venue = str(self.rng.choice(self.venues))
        if reason == "profit_target":
            return Fill(price, notional * self.cost.fee_bps(True) / 1e4, True, venue, 0.0, "limit")
        slip = self.cost.slippage_bps(notional, bar_notional, maker=False)
        px = price * (1 - side * slip / 1e4)
        return Fill(px, notional * self.cost.fee_bps(False) / 1e4, False, venue, slip, "market")
