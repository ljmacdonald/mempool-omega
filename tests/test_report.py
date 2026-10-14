"""The weekly public report and the share cards (D102)."""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent.parent / "infra" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


R = _load("report")
C = _load("cards")
MON = datetime(2026, 10, 12, 0, 5, tzinfo=timezone.utc)


def summary(n, avg, random, equity=100.0, when=MON):
    return {"generated_at": str(when), "sections": [
        {"key": "main", "name": "Exchange coins", "path": "coins/", "n": n, "avg": avg, "random": random, "unit": "%", "status": "behind"},
        {"key": "challenge", "name": "$100 DEX challenge", "path": "challenge/", "n": 1, "avg": 0.0, "random": 0.0, "unit": "%",
         "status": "early", "equity": equity, "open": 0, "paused": False}]}


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "STATE", tmp_path)
    monkeypatch.setattr(C, "STATE", tmp_path)
    monkeypatch.setattr(C, "OUT", tmp_path / "cards")
    return tmp_path


def run(state, s, now):
    (state / "summary.json").write_text(json.dumps(s))
    return R.update(now)


def test_week_figures_come_from_the_difference_of_two_frozen_records(state):
    run(state, summary(100, 0.01, 0.0, 100.0), MON)
    d = run(state, summary(100, 0.01, 0.0, 100.0), MON + timedelta(days=3))
    assert len(d["snapshots"]) == 1 and d["weeks"] == []                # same week: frozen once
    d = run(state, summary(150, 0.0, 0.002, 103.5), MON + timedelta(days=7, minutes=1))
    assert len(d["snapshots"]) == 2
    w = d["weeks"][0]
    main = next(s for s in w["sections"] if s["key"] == "main")
    assert main["trades"] == 50
    assert main["avg"] == pytest.approx((0.0 * 150 - 0.01 * 100) / 50)  # the week's 50 trades averaged −2%
    assert main["random"] == pytest.approx(0.002 * 150 / 50)
    ch = next(s for s in w["sections"] if s["key"] == "challenge")
    assert (ch["equity_from"], ch["equity_to"]) == (100.0, 103.5)
    assert "equity_from" not in main


def test_a_record_that_shrank_gets_no_weekly_figure(state):
    run(state, summary(500, 0.01, 0.0), MON)
    d = run(state, summary(160, -0.02, -0.03), MON + timedelta(days=7))
    main = next(s for s in d["weeks"][0]["sections"] if s["key"] == "main")
    assert main["trades"] is None and main["avg"] is None


def test_week_so_far_against_the_live_summary(state):
    run(state, summary(100, 0.01, 0.0), MON)
    d = run(state, summary(110, 0.0, 0.0), MON + timedelta(days=2))
    sf = d["so_far"]
    assert sf["so_far"] and next(s for s in sf["sections"] if s["key"] == "main")["trades"] == 10


def test_cards_are_made_and_then_not_due_for_12_hours(state):
    pytest.importorskip("PIL")
    (state / "summary.json").write_text(json.dumps(summary(100, -0.01, 0.0, 98.5)))
    (state / "dex" / "challenge").mkdir(parents=True)
    (state / "dex" / "challenge" / "equity.csv").write_text("t,equity,cash,open\nx,100,100,0\ny,99,90,1\nz,98.5,98.5,0\n")
    assert C.due(MON)
    made = C.make(MON)
    assert made == ["results.png", "challenge.png"]
    from PIL import Image
    assert Image.open(state / "cards" / "results.png").size == (1200, 630)
    assert not C.due(MON + timedelta(hours=11)) and C.due(MON + timedelta(hours=12))
