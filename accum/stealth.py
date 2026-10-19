"""Stealth accumulation (D120): the owner's pattern. A coin goes quiet; someone buys it in small chunks over many
days (selling a little now and then to keep up appearances) until they hold a meaningful share of the supply; then
liquidity is added, the price is pumped to pull buyers in, and they sell into it. The aim: spot the quiet buying,
buy on paper before the pump, and get out before the dump. Pure functions: no network, no files.

Swaps are read from the token's own transfer history (Ethereum, Etherscan): inside one transaction, if a known pool
sent tokens out, the addresses whose balance went up are buyers; if a pool took tokens in, the addresses whose balance
went down are sellers. Routers and aggregators pass tokens through and net to zero, so they drop out by themselves.
A transaction with no pool involved is a plain transfer: never counted as buying.

Everything is evaluated one finished day at a time, using only data up to that day (no lookahead).
"""
from __future__ import annotations

from collections import defaultdict, deque

ZERO = "0x0000000000000000000000000000000000000000"
DEAD = {ZERO, "0x000000000000000000000000000000000000dead"}
# shared pool contracts (Uniswap v4 PoolManager, Balancer v2/v3 vaults) hold many pools' tokens
SHARED_POOLS = {"0x000000000004444c5dc75cb358380d2e3de08a90": "Uniswap v4", "0xba12222222228d8ba445958a75a0704d566bf2c8": "Balancer v2",
                "0xba1333333333a1ba1108e8412f11850a5c319ba9": "Balancer v3"}
ROUTERS = {"0x7a250d5630b4cf539739df2c5dacb4c659f2488d", "0xe592427a0aece92de3edee1f18e0157c05861564", "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45",
           "0xef1c6e67703c7bd7107eed8303fbe6ec2554bf6b", "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad", "0x66a9893cc07d91d95644aedd05d03f95e1dba8af",
           "0x1111111254eeb25477b68fb85ed929f73a960582", "0x111111125421ca6dc452d289314280a0f8842a65", "0xdef1c0ded9bec7f1a1670819833240f027b25eff",
           "0x9008d19f58aabd9ed0d60971565aa8510560ab41", "0xdef171fe48cf0115b1d80b88dc8eab59176fee57", "0x6a000f20005980200259b80c5102003040001068",
           "0xd9e1ce17f2641f24ae83637ab66a2cca9c378b9f", "0x6131b5fae19ea4f9d964eac0408e4408b66337b5", "0x881d40237659c251811cec9c364ef91dc08d300c"}

# the signal, fixed before any result (config lives here so the rules and the replay can't drift apart)
RULES = {
    "window_d": 60,              # look back 60 days
    "min_buy_days": 5,           # an accumulator buys on at least 5 separate days in the window...
    "max_sold": 0.30,            # ...selling at most 30% of what it bought (a few cover sales are allowed)
    "max_chunk_usd": 5_000,      # ...in small chunks: its median buy is $5,000 or less
    "max_chunk_share": 0.00005,  # (when the price is unknown: median buy at most 0.005% of the supply)
    "min_wallet_share": 0.0005,  # ...and has net bought at least 0.05% of the circulating supply
    "min_total_share": 0.005,    # the accumulators together hold at least 0.5% of the supply bought this way
    "quiet": (-0.30, 0.30),      # the price over the last 30 days moved between -30% and +30% (not pumping yet)
    "cooldown_d": 60,            # one signal per token per 60 days
}
TRADE = {"take_profit": 0.60, "stop": 0.30, "dump": 0.25, "max_days": 90, "cost": 0.01}
# exit when: +60% (take the pump), -30% (safety exit), the accumulators sold 25% of what they held (the dump has
# started), or 90 days; 1% round-trip cost (pool fees both ways and slippage). Reward 2x the risk.


def classify(tx_transfers: list[dict], pools: set[str]) -> tuple[dict[str, float], dict[str, float]]:
    """One transaction's transfers of the token -> (buyers {address: qty}, sellers {address: qty})."""
    net: dict[str, float] = defaultdict(float)
    for t in tx_transfers:
        net[t["from"]] -= t["qty"]
        net[t["to"]] += t["qty"]
    pool_net = sum(v for a, v in net.items() if a in pools)
    if pool_net == 0:
        return {}, {}
    others = {a: v for a, v in net.items() if a not in pools and a not in ROUTERS and a not in DEAD and abs(v) > 0}
    if pool_net < 0:                                     # a pool paid tokens out: whoever gained them bought
        return {a: v for a, v in others.items() if v > 0}, {}
    return {}, {a: -v for a, v in others.items() if v < 0}


class Tracker:
    """Rolling 60-day per-address buying, evaluated once per finished day. State is plain dicts (saved as JSON)."""

    def __init__(self, state: dict | None = None):
        s = state or {}
        self.days: dict[str, deque] = {a: deque(v) for a, v in (s.get("days") or {}).items()}   # addr -> [[day, buy, sell, n, [chunks]]]
        self.today: dict[str, list] = s.get("today") or {}                                        # this unfinished day
        self.day: str | None = s.get("day")
        self.last_signal: str | None = s.get("last_signal")
        self.group: dict[str, str] = s.get("group") or {}        # address -> group id (same funder), probable only

    def to_state(self) -> dict:
        return {"days": {a: list(v) for a, v in self.days.items()}, "today": self.today, "day": self.day,
                "last_signal": self.last_signal, "group": self.group}

    def add(self, day: str, buyers: dict, sellers: dict):
        for a, q in buyers.items():
            r = self.today.setdefault(a, [0.0, 0.0, 0, []])
            r[0] += q
            r[2] += 1
            r[3].append(q)
        for a, q in sellers.items():
            if a in self.today or a in self.days:            # sells only matter for addresses that have bought
                r = self.today.setdefault(a, [0.0, 0.0, 0, []])
                r[1] += q

    def close_day(self, window: int = RULES["window_d"]):
        """Move the finished day into the window and drop days older than the window."""
        if self.day is None:
            return
        for a, (b, s, n, ch) in self.today.items():
            self.days.setdefault(a, deque()).append([self.day, b, s, n, ch[:50]])
        self.today = {}
        cut = _minus(self.day, window - 1)
        for a in list(self.days):
            dq = self.days[a]
            while dq and dq[0][0] < cut:
                dq.popleft()
            if not dq:
                del self.days[a]

    def accumulators(self, supply: float | None, price: float | None) -> list[dict]:
        R = RULES
        out = []
        for a, dq in self.days.items():
            buy_days = sum(1 for d in dq if d[1] > 0)
            if buy_days < R["min_buy_days"]:
                continue
            bought = sum(d[1] for d in dq)
            sold = sum(d[2] for d in dq)
            if bought <= 0 or sold > R["max_sold"] * bought:
                continue
            chunks = sorted(c for d in dq for c in d[4])
            med = chunks[len(chunks) // 2] if chunks else 0
            small = (med * price <= R["max_chunk_usd"]) if price else (supply and med <= R["max_chunk_share"] * supply)
            if not small:
                continue
            share = (bought - sold) / supply if supply else None
            if share is None or share < R["min_wallet_share"]:
                continue
            out.append({"address": a, "group": self.group.get(a, a), "buy_days": buy_days, "bought": bought, "sold": sold,
                        "net": bought - sold, "share": share, "median_buy": med, "median_buy_usd": med * price if price else None,
                        "first_day": dq[0][0], "buys": sum(d[3] for d in dq)})
        return sorted(out, key=lambda x: -x["net"])


def signal(acc: list[dict], supply: float | None, quiet_change: float | None, day: str, last_signal: str | None) -> dict | None:
    """The token-level signal for a finished day, or None."""
    R = RULES
    if not acc or not supply:
        return None
    if last_signal and day < _plus(last_signal, R["cooldown_d"]):
        return None
    total = sum(x["net"] for x in acc) / supply
    if total < R["min_total_share"]:
        return None
    if quiet_change is None or not (R["quiet"][0] <= quiet_change <= R["quiet"][1]):
        return None
    groups = {x["group"] for x in acc}
    return {"day": day, "share": total, "accumulators": len(acc), "groups": len(groups), "quiet_change": quiet_change,
            "top": acc[:8], "buy_days": max(x["buy_days"] for x in acc), "since": min(x["first_day"] for x in acc)}


LP_ADD = ("addliquidity", "increaseliquidity", "mint(", "joinpool", "add_liquidity", "modifyliquidit")
LP_REMOVE = ("removeliquidity", "decreaseliquidity", "burn(", "exitpool", "remove_liquidity", "collect(")


def liquidity_kind(function_name: str) -> str | None:
    """'add' or 'remove' when the transaction is a liquidity operation (from the called function's name), else None.
    Calls through a multi-purpose router can't be told apart and are read as swaps (a stated limit)."""
    fn = function_name.lower()
    if any(k in fn for k in LP_REMOVE):
        return "remove"
    if any(k in fn for k in LP_ADD):
        return "add"
    return None


def note_liquidity(st: dict, tr: Tracker, day: str, kind: str, grp: list[dict], pools: set[str]):
    """Record a liquidity add/removal: who did it and how much moved, kept for the 'merged into pools' step."""
    qty = sum(t["qty"] for t in grp if (t["to"] in pools if kind == "add" else t["from"] in pools))
    side = "from" if kind == "add" else "to"           # the provider sends tokens in, or receives them back
    who = sorted({t[side] for t in grp} - pools - ROUTERS - DEAD)
    ev = {"day": day, "kind": kind, "qty": qty, "by": who[:5]}
    st.setdefault("lp", []).append(ev)
    st["lp"] = st["lp"][-300:]


def simulate(entry_day: str, prices: dict[str, float], acc_sold_by_day: dict[str, float], acc_net: float) -> dict | None:
    """The paper trade for a signal: buy at the next day's close, exit on +60%, -30%, the accumulators selling 25% of
    their holding, or 90 days. prices: day -> close. acc_sold_by_day: day -> the accumulators' net selling that day."""
    days = sorted(d for d in prices if d > entry_day)
    if not days:
        return None
    d0 = days[0]
    p0 = prices[d0]
    if not p0:
        return None
    sold, best, worst = 0.0, 0.0, 0.0
    T = TRADE
    for d in days[1:]:
        p = prices[d]
        if not p:
            continue
        ch = p / p0 - 1
        best, worst = max(best, ch), min(worst, ch)
        sold += acc_sold_by_day.get(d, 0.0)
        reason = ("take profit (+60%)" if ch >= T["take_profit"] else "safety exit (-30%)" if ch <= -T["stop"] else
                  "the accumulators started selling" if acc_net > 0 and sold >= T["dump"] * acc_net else
                  f"held {T['max_days']} days" if _days(d0, d) >= T["max_days"] else None)
        if reason:
            return {"entry_day": d0, "entry": p0, "exit_day": d, "exit": p, "reason": reason, "ret": ch - T["cost"],
                    "best": best, "worst": worst, "days": _days(d0, d)}
    return {"entry_day": d0, "entry": p0, "exit_day": None, "exit": None, "reason": "still open", "ret": None, "best": best,
            "worst": worst, "days": _days(d0, days[-1])}


def simulate_price_only(entry_day: str, prices: dict[str, float]) -> float | None:
    """The same trade on a random coin (no accumulators to watch): +60%, -30% or 90 days, after costs."""
    r = simulate(entry_day, prices, {}, 0.0)
    return r["ret"] if r else None


def _days(a: str, b: str) -> int:
    from datetime import date
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _minus(day: str, n: int) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(day) - timedelta(days=n)).isoformat()


def _plus(day: str, n: int) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()
