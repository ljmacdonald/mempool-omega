"""The summary of every section and the watchdog (D98)."""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("summary", Path(__file__).resolve().parent.parent / "infra" / "summary.py")
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)

NOW = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)          # a Monday, US market and forex open


@pytest.mark.parametrize("n,avg,rnd,unit,want", [
    (10, 0.05, 0.0, "%", "early"),
    (100, 0.012, 0.002, "%", "ahead"),            # made money, clearly beat random
    (100, 0.0003, 0.0002, "%", "even"),           # positive but no better than random
    (100, -0.027, -0.030, "%", "behind"),         # lost money (random picks lost a little more)
    (100, -0.01, -0.05, "%", "behind"),           # lost money, even though random picks lost more
    (100, 0.2, 0.05, "R", "ahead"),
])
def test_status(n, avg, rnd, unit, want):
    assert S.status_of(n, avg, rnd, unit) == want


def test_merge_weights_by_finished_trades():
    m = S.merge({"n": 100, "avg": 0.01, "random": 0.0}, {"n": 300, "avg": -0.01, "random": 0.0})
    assert m["n"] == 400 and m["avg"] == pytest.approx(-0.005)


def test_sections_flag_stale_pages_but_not_closed_markets(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "STATE", tmp_path)
    fresh, old = str(NOW - timedelta(minutes=20)), str(NOW - timedelta(hours=9))
    (tmp_path / "suggestions").mkdir()
    (tmp_path / "suggestions/scoreboard.json").write_text(json.dumps({"closed": 200, "avg_return_per_idea": 0.01,
                                                                      "random_pick_avg_return": 0.0}))
    (tmp_path / "suggestions/latest.json").write_text(json.dumps({"generated_at": old}))
    (tmp_path / "suggestions/small_latest_day.json").write_text(json.dumps({"generated_at": fresh}))
    monkeypatch.setattr(S, "read_data", lambda rel: {"generated_at": fresh})
    secs = {s["key"]: s for s in S.sections(NOW)}
    assert secs["main"]["stale"] and secs["main"]["status"] == "ahead"
    assert not secs["small"]["stale"] and not secs["stocks"]["stale"]
    sat = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(S, "read_data", lambda rel: {"generated_at": str(sat - timedelta(days=1))})
    secs = {s["key"]: s for s in S.sections(sat)}
    assert secs["stocks"]["market_closed"] and not secs["stocks"]["stale"]       # weekend: old data is expected
    assert secs["ict"]["stale"]                                                  # crypto runs every day


def test_never_touches_github_outside_the_scheduled_job(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "STATE", tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(S, "read_data", lambda rel: None)
    called = []
    monkeypatch.setattr(S.urllib.request, "urlopen", lambda *a, **k: called.append(a))
    assert S.main() == 0 and not called
    assert json.loads((tmp_path / "summary.json").read_text())["problems"]


@pytest.mark.parametrize("n,avg,rnd,unit,verdict,hedged", [
    (100, -0.0051, -0.0079, "%", "beats", 0.0028 - 0.001),    # lost money, but beat the market by more than the margin
    (100, -0.0050, -0.0023, "%", "lags", -0.0027 - 0.001),
    (100, -0.0013, -0.0017, "%", "level", 0.0004 - 0.001),
    (100, 0.0030, 0.0009, "%", "level", 0.0021 - 0.001),      # 0.21%: under the 0.25% margin
    (10, 0.05, 0.0, "%", "early", 0.05 - 0.001),
    (100, -0.03, -0.24, "R", "beats", None),                   # in R there is no hedge cost
])
def test_picks_against_the_market(n, avg, rnd, unit, verdict, hedged):
    v = S.vs_market(n, avg, rnd, unit)
    assert v["verdict"] == verdict and v["edge"] == pytest.approx(avg - rnd)
    assert (v["hedged"] is None) if hedged is None else v["hedged"] == pytest.approx(hedged)
    assert S.vs_market(100, None, 0.0) is None
