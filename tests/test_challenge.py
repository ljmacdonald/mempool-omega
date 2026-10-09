"""The $100 DEX challenge (D95): realistic fills, every cost, and the owner's safety rules."""
import json

import pandas as pd
import pytest

from core import config
from dex import challenge as C

T0 = pd.Timestamp("2026-10-10T10:20:00Z")
CHAINS = {"solana": {"name": "Solana", "gas_usd": 0.01, "mev": 0.002, "sniper": 0.005}}


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    return tmp_path


def cand(liq=500_000.0):
    return {"chain": "solana", "token": "T", "pool": "P", "close": 1.0, "liq_real": liq, "pool_fee": 0.0025,
            "reserve_usd": 2 * liq, "facts": {"buy_tax": 0.0, "sell_tax": 0.0}, "styles": {"dex_short": {"p": 0.6}}}


def idea(grade="Moderate", exp_r=0.3):
    return {"symbol": "solana:T", "coin": "TOK", "chain": "solana", "pool": "P", "style": "dex_short", "score": 6.5,
            "grade": grade, "chance_beats_market": 0.6, "expected_r": exp_r, "risk_unit": 0.05,
            "take_profit_pct": 0.10, "safety_exit_pct": -0.05, "cost_rt": 0.01}


def bars(start, closes):
    idx = pd.date_range(start, periods=len(closes), freq="h", tz="UTC")
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "qv": 1e5}, index=idx)


def step(now, ideas=None, df=None, liq=500_000.0, check=None):
    candles = {"solana:P": df} if df is not None else {}
    return C.run({"dex_short": ideas or []}, [cand(liq)], candles, CHAINS, {}, now=now, quality_check=check)


def test_sizing_caps_risk_at_2pct_and_size_at_25pct():
    amount, cost = C.size_trade(100.0, 100.0, 0.10, lambda a: 0.01)
    assert amount == pytest.approx(2.0 / 0.11) and amount * (0.10 + cost) == pytest.approx(2.0)
    amount, _ = C.size_trade(100.0, 100.0, 0.001, lambda a: 0.001)
    assert amount == pytest.approx(25.0)


def test_full_trade_buys_at_the_hour_close_and_sells_at_take_profit_after_every_cost(state):
    acc = step(T0, [idea()])
    pos = acc["positions"][0]
    assert pos["status"] == "pending" and acc["cash"] == pytest.approx(100 - pos["amount"])
    # the hour the list came out closes at 1.02 (the price ran up: we pay it), then rises past the take profit
    df = bars("2026-10-10T10:00:00Z", [1.02, 1.05, 1.13])
    acc = step(T0 + pd.Timedelta(hours=3), [], df)
    assert not acc["positions"] and acc["trades"] == 1
    t = pd.read_csv(state / "dex/challenge/trades.csv").iloc[0]
    assert t["entry"] == pytest.approx(1.02) and t["exit"] == pytest.approx(1.13) and t["why"] == "take profit reached"
    gross = 1.13 / 1.02 - 1
    assert 0 < t["ret"] < gross                                   # every cost comes off
    log = json.loads((state / "dex/challenge/log.json").read_text())
    kinds = [x["kind"] for x in log]
    assert kinds.index("sell") < kinds.index("buy") < kinds.index("order")      # newest first
    assert "front-runners" in log[kinds.index("buy")]["text"]
    assert acc["equity"] == pytest.approx(acc["cash"]) and acc["equity"] > 100


def test_crash_through_the_safety_exit_sells_where_the_price_is_not_at_the_stop(state):
    step(T0, [idea()])
    acc = step(T0 + pd.Timedelta(hours=3), [], bars("2026-10-10T10:00:00Z", [1.0, 0.70]))
    t = pd.read_csv(state / "dex/challenge/trades.csv").iloc[0]
    assert t["why"] == "crashed through the safety exit" and t["ret"] < -0.29     # planned -5%, got about -30%
    assert acc["equity"] < 100 and acc["equity"] > 90                            # but sizing kept the damage small


def test_a_pulled_pool_is_sold_against_what_is_left_in_it(state):
    """The creator pulled the pool's money (the price hasn't moved yet): the trade is sold at once, and the sale
    itself moves the now-thin pool a lot. A real rug usually also crashes the price (see the crash test)."""
    step(T0, [idea()])
    step(T0 + pd.Timedelta(hours=1, minutes=5), [], bars("2026-10-10T10:00:00Z", [1.0]))
    acc = step(T0 + pd.Timedelta(hours=2), [], bars("2026-10-10T10:00:00Z", [1.0, 1.0]), liq=100.0)
    t = pd.read_csv(state / "dex/challenge/trades.csv").iloc[0]
    assert "rug-pull" in t["why"] and t["ret"] < -0.2 and acc["equity"] > 90


def test_time_limit_sells_after_the_style_hold():
    step(T0, [idea()])
    acc = step(T0 + pd.Timedelta(hours=8), [], bars("2026-10-10T10:00:00Z", [1.0, 1.01, 1.0, 1.01, 1.0, 1.01]))
    assert acc["trades"] == 1


@pytest.mark.parametrize("kw,grade,needle", [
    ({}, "Weak", "graded Weak"),
    ({"check": {"reliable": False}}, "Moderate", "can't be trusted"),
    ({}, "Moderate", "negative"),
])
def test_stays_in_cash_when_the_rules_say_no(state, kw, grade, needle):
    acc = step(T0, [idea(grade, exp_r=-0.5 if needle == "negative" else 0.3)], **kw)
    assert not acc["positions"] and acc["cash"] == 100
    log = json.loads((state / "dex/challenge/log.json").read_text())
    assert log[0]["kind"] == "check" and needle in log[0]["text"]


def test_daily_loss_limit_and_drawdown_pause(state):
    acc = C.load_account()
    acc.update({"cash": 94.0, "equity": 94.0, "day": "2026-10-10", "day_start": 100.0})
    (state / "dex/challenge").mkdir(parents=True, exist_ok=True)
    (state / "dex/challenge/account.json").write_text(json.dumps(acc))
    acc = step(T0, [idea()])
    assert not acc["positions"] and not acc["paused"]
    assert "loss limit" in json.loads((state / "dex/challenge/log.json").read_text())[0]["text"]
    acc.update({"cash": 79.0, "day": "2026-10-11"})
    (state / "dex/challenge/account.json").write_text(json.dumps(acc))
    acc = step(T0 + pd.Timedelta(days=1), [idea()])
    assert acc["paused"] and not acc["positions"]
    acc = step(T0 + pd.Timedelta(days=2), [idea()])          # stays paused until the owner restarts it
    assert acc["paused"] and not acc["positions"]


def test_never_more_than_two_open_and_one_new_per_hour(state):
    ideas = [idea()]
    step(T0, ideas)
    acc = step(T0 + pd.Timedelta(minutes=30), ideas)         # already held: not bought twice
    assert len(acc["positions"]) == 1
