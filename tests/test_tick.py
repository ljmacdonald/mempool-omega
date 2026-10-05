"""The tick job starts exactly the jobs that are due."""
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location("tick", Path(__file__).resolve().parent.parent / "infra" / "tick.py")
T = importlib.util.module_from_spec(spec)
sys.modules["tick"] = T
spec.loader.exec_module(T)

MON_10 = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)      # a Monday


def run(mins_ago, title="", status="completed", now=MON_10):
    return {"created_at": (now - timedelta(minutes=mins_ago)).isoformat(), "status": status, "display_title": title}


def job(file, task):
    return next(j for j in T.JOBS if j.file == file and j.task == task)


def test_regular_jobs():
    ict = job("ict.yml", "scan")
    assert T.due(ict, [run(20)], MON_10) and not T.due(ict, [run(5)], MON_10)
    assert T.due(ict, [], MON_10)
    assert not T.due(ict, [run(1, status="in_progress"), run(60)], MON_10)        # one already running
    paper = job("paper.yml", None)
    assert T.due(paper, [run(57)], MON_10) and not T.due(paper, [run(30)], MON_10)


def test_nightly_runs_dont_count_as_regular_and_vice_versa():
    ict = job("ict.yml", "scan")
    assert T.due(ict, [run(2, "ICT: backtest"), run(40, "ICT: scan")], MON_10)
    bt = job("ict.yml", "backtest")
    at = MON_10.replace(hour=5, minute=0)
    assert T.due(bt, [run(5, "ICT: scan", now=at)], at)
    assert not T.due(bt, [run(5, "ICT: 50 4 * * *", now=at)], at)
    assert not T.due(bt, [], MON_10.replace(hour=4, minute=0))                 # too early
    assert not T.due(bt, [], MON_10.replace(hour=12, minute=0))                # too late: wait for tomorrow


def test_market_windows():
    fx, st = job("fx.yml", "scan"), job("stocks.yml", "scan")
    sat = MON_10 - timedelta(days=2)
    sun_eve = MON_10 - timedelta(days=1) + timedelta(hours=12)              # Sunday 22:00 UTC
    assert not T.due(fx, [], sat) and T.due(fx, [], sun_eve) and T.due(fx, [], MON_10)
    assert not T.due(st, [], MON_10) and T.due(st, [], MON_10.replace(hour=14))
    tr = job("stocks.yml", "train")
    assert not T.due(tr, [], sat.replace(hour=22))                            # weekdays only
