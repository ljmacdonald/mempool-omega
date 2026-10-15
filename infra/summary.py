"""One summary of every section, and a watchdog (DECISIONS D98).

Every 15 minutes (started by infra/tick.py) this reads each section's own published record and freshness and writes
state/summary.json, which the Results page, the System status page and every page's "out of date" banner read.

Record status, per section, after every cost and against the section's own random-pick baseline:
  ahead   enough finished trades (MIN_N), made money, and beat random picks by a clear margin (MARGIN)
  behind  enough finished trades and lost money (even if random picks lost more)
  even    enough finished trades and made money, but not clearly more than random picks did
  early   too few finished trades to say anything
This is a plain summary of each page's own numbers, not a test of skill: a section can be "ahead" by luck, and the
pages themselves carry the stricter checks (probation, the grade check, walk-forward tests).

Watchdog: a section is out of date when its data is older than it should be (stocks and forex only while their
markets are open), and a job is failing when its last two runs failed. Problems open (or update) one GitHub issue,
which emails the owner; the issue closes itself when everything is fine again.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get("OMEGA_STATE_DIR") or ROOT / "state")
RAW = "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/data/"
MIN_N = 30
MARGIN = {"%": 0.0025, "R": 0.10}     # "clearly better than random": 0.25% per trade, or 0.1 R
ISSUE_TITLE = "Watchdog: part of the product needs attention"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_t(s) -> datetime | None:
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00").replace(" ", "T", 1))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def read_state(rel: str):
    p = STATE / rel
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def read_data(rel: str):
    """A page snapshot from the data branch: the local clone if fetched, else the public raw file."""
    try:
        out = subprocess.run(["git", "show", f"origin/data:{rel}"], cwd=ROOT, capture_output=True, text=True, timeout=30)
        if out.returncode == 0:
            return json.loads(out.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    try:
        with urllib.request.urlopen(RAW + rel, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception:  # noqa: BLE001
        return None


def market_open(window: str, now: datetime) -> bool:
    import sys
    sys.path.insert(0, str(ROOT / "infra"))
    from tick import in_window

    return in_window(window, now)


def status_of(n, avg, random, unit: str = "%") -> str:
    if not n or n < MIN_N or avg is None:
        return "early"
    if avg < 0:
        return "behind"
    return "ahead" if avg - (random or 0.0) >= MARGIN[unit] else "even"


def board(rel: str) -> dict:
    b = read_state(rel) or {}
    return {"n": b.get("closed") or 0, "avg": b.get("avg_return_per_idea"), "random": b.get("random_pick_avg_return"),
            "win": b.get("win_rate"), "check": ((b.get("quality") or {}).get("check") or {}).get("reliable")}


def merge(*bs: dict) -> dict:
    """Combine records weighted by their number of finished trades."""
    n = sum(b["n"] for b in bs)
    if not n:
        return {"n": 0, "avg": None, "random": None, "win": None, "check": None}
    w = lambda k: (sum(b[k] * b["n"] for b in bs if b.get(k) is not None) /  # noqa: E731
                   max(sum(b["n"] for b in bs if b.get(k) is not None), 1)) if any(b.get(k) is not None for b in bs) else None
    return {"n": n, "avg": w("avg"), "random": w("random"), "win": w("win"), "check": None}


def per_rule(record: dict, n="n", avg="avg", random="random") -> dict:
    rows = [{"n": v.get(n) or 0, "avg": v.get(avg), "random": v.get(random)} for v in (record or {}).values()
            if isinstance(v, dict)]
    return merge(*rows) if rows else {"n": 0, "avg": None, "random": None, "win": None, "check": None}


def sections(now: datetime) -> list[dict]:
    out = []

    def add(key, name, path, rec, updated, max_age_min, unit="%", window=None, note=""):
        t = parse_t(updated)
        age = round((now - t).total_seconds() / 60) if t else None
        watched = window is None or market_open(window, now)
        stale = bool(watched and (age is None or age > max_age_min))
        out.append({"key": key, "name": name, "path": path, "n": rec.get("n", 0), "avg": rec.get("avg"),
                    "random": rec.get("random"), "win": rec.get("win"), "unit": unit,
                    "status": rec.get("status") or status_of(rec.get("n"), rec.get("avg"), rec.get("random"), unit),
                    "updated": str(t) if t else None, "age_min": age, "max_age_min": max_age_min,
                    "market_closed": not watched, "stale": stale, "note": note})

    add("main", "Exchange coins", "coins/", board("suggestions/scoreboard.json"),
        (read_state("suggestions/latest.json") or {}).get("generated_at"), 150)
    add("small", "Small coins", "small/", board("suggestions/small_scoreboard.json"),
        (read_state("suggestions/small_latest_day.json") or {}).get("generated_at"), 150)
    add("dex", "DEX tokens", "dex/", board("dex/scoreboard.json"),
        (read_state("dex/snapshot.json") or {}).get("generated_at"), 150)
    s = read_data("stocks/snapshot.json") or {}
    add("stocks", "US stocks", "stocks/", merge(board("stocks/scoreboard_large.json"), board("stocks/scoreboard_volatile.json")),
        s.get("generated_at"), 60, window="us_stocks")
    s = read_data("fx/snapshot.json") or {}
    add("fx", "Forex & gold", "fx/", board("fx/scoreboard.json"), s.get("generated_at"), 60, window="fx")
    s = read_data("ict/snapshot.json") or {}
    add("ict", "ICT setups", "ict/", per_rule(s.get("record"), n="filled", avg="avg_r", random="random_avg_r"),
        s.get("generated_at"), 60, unit="R", note="Results per $1 risked (R), not per $1 invested.")
    s = read_data("lab/snapshot.json") or {}
    add("lab", "Strategy lab", "lab/", per_rule(s.get("record")), s.get("generated_at"), 60)
    s = read_data("listings/snapshot.json") or {}
    add("listings", "New listings", "listings/", per_rule(s.get("record")), s.get("generated_at"), 90)
    s = read_data("sniper/snapshot.json") or {}
    r = ((s.get("results") or {}).get(s.get("plan") or "standard") or {}).get("passed") or {}
    add("sniper", "Sniper lab", "sniper/", {"n": r.get("n") or 0, "avg": r.get("avg"), "random": r.get("random")},
        s.get("generated_at"), 60, note="Coins that passed the strict checks; the random line is the rejected coins.")
    s = read_data("predict/snapshot.json") or {}
    r = s.get("record") or {}
    add("predict", "Prediction markets", "predict/", {"n": r.get("settled") or 0, "avg": r.get("avg"), "random": 0.0,
                                                     "win": r.get("won")}, s.get("generated_at"), 150,
        note="Per $1 staked after fees; break-even is the bar.")
    s = read_data("whales/snapshot.json") or {}
    v = s.get("verdict") or {}
    days = v.get("days") or 0
    add("whales", "Whale tracker", "whales/", {"n": days, "avg": v.get("lead"), "random": v.get("rand"),
                                               "status": "early" if days < 30 else status_of(MIN_N, v.get("lead"), v.get("rand"))},
        s.get("generated_at"), 60, note="Finished = days of paper-copying the whales (a verdict needs 30); figures are its total return so far vs random picks.")
    s = read_data("mev/snapshot.json") or {}
    add("mev", "Arbitrage lab", "mev/", {"n": (s.get("counts") or {}).get("opportunities") or 0, "avg": None,
                                         "random": None, "status": "early"}, s.get("generated_at"), 150,
        note="Shadow research: do Solana arbitrage opportunities survive long enough to trade? No trades are made.")
    for key, name, path, rel in (("challenge", "$100 DEX challenge", "challenge/", "dex/challenge"),
                                 ("challenge-small", "$100 Small-coin challenge", "challenge-small/", "suggestions/challenge_small"),
                                 ("challenge-arb", "$100 arbitrage account", "mev/", "mev/account")):
        a = read_state(f"{rel}/account.json") or {}
        eq, start = a.get("equity"), a.get("start") or 100.0
        add(key, name, path, {"n": a.get("trades") or 0, "avg": (eq / start - 1) if eq else None, "random": 0.0,
                              "status": "early" if (a.get("trades") or 0) < 20 else status_of(MIN_N, (eq or start) / start - 1, 0.0),
                              "equity": eq}, a.get("updated"), 150,
            note=f"Account value ${eq:,.2f} from $100." if eq else "Starts with the next hourly check.")
        out[-1]["equity"] = eq
        out[-1]["paused"] = bool(a.get("paused"))
        out[-1]["open"] = len(a.get("positions") or [])
    return out


def failing_jobs(token: str | None, repo: str) -> list[dict]:
    """Workflows whose last two finished runs failed."""
    if not token:
        return []
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/runs?per_page=100&status=completed",
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            runs = json.loads(r.read().decode()).get("workflow_runs", [])
    except Exception:  # noqa: BLE001
        return []
    by: dict[str, list] = {}
    for run in runs:                                          # newest first
        by.setdefault(run["path"], []).append(run)
    out = []
    for path, rs in by.items():
        last = [r for r in rs if r["conclusion"] in ("success", "failure")][:2]
        if len(last) == 2 and all(r["conclusion"] == "failure" for r in last):
            out.append({"workflow": rs[0]["name"].split(":")[0], "path": path, "since": last[1]["created_at"],
                        "url": last[0]["html_url"]})
    return out


def sync_issue(token: str | None, repo: str, problems: list[str]) -> None:
    """One open issue while anything is wrong (GitHub emails the owner); closed when all is well."""
    if not token:
        return
    api = f"https://api.github.com/repos/{repo}"
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json"}

    def call(method, url, body=None):
        req = urllib.request.Request(url, method=method, headers=hdr,
                                     data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "null")

    try:
        open_ = [i for i in call("GET", f"{api}/issues?state=open&per_page=100") or []
                 if i.get("title") == ISSUE_TITLE and "pull_request" not in i]
        if problems:
            body = ("The watchdog found:\n\n" + "\n".join(f"- {p}" for p in problems) +
                    "\n\nThis issue updates itself every 15 minutes and closes when everything is fine again. "
                    "See the System status page: https://ljmacdonald.github.io/mempool-omega/status/\n\n---\n"
                    "_Generated by [Claude Code](https://claude.ai/code)_")
            if open_:
                if open_[0].get("body") != body:
                    call("PATCH", f"{api}/issues/{open_[0]['number']}", {"body": body})
            else:
                call("POST", f"{api}/issues", {"title": ISSUE_TITLE, "body": body})
        else:
            for i in open_:
                call("PATCH", f"{api}/issues/{i['number']}", {"state": "closed", "state_reason": "completed"})
    except Exception as e:  # noqa: BLE001
        print("watchdog issue:", e)


def main() -> int:
    now = now_utc()
    # only the project's own scheduled job talks to GitHub (never a developer's machine or a test)
    token = os.environ.get("GITHUB_TOKEN") if os.environ.get("GITHUB_ACTIONS") == "true" else None
    repo = os.environ.get("GITHUB_REPOSITORY", "ljmacdonald/mempool-omega")
    secs = sections(now)
    jobs = failing_jobs(token, repo)
    problems = [f"{s['name']} data is {s['age_min']} minutes old (should be under {s['max_age_min']})" if s["age_min"] is not None
                else f"{s['name']} has no data" for s in secs if s["stale"]]
    problems += [f"The '{j['workflow']}' job failed its last two runs (since {j['since']}): {j['url']}" for j in jobs]
    summary = {"generated_at": str(now), "min_n": MIN_N, "sections": secs, "failing_jobs": jobs, "problems": problems,
               "ok": not problems}
    (STATE / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    sync_issue(token, repo, problems)
    print(f"summary: {len(secs)} sections, {len(problems)} problem(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
