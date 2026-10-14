"""The weekly public report (DECISIONS D102).

Run right after infra/summary.py (every 15 minutes). The first run of each week (weeks start Monday 00:00 UTC) freezes
a copy of every section's record from state/summary.json into state/reports/weekly.json. Two neighbouring copies give
one finished week: how many trades finished that week and their average after costs, against random picks over the
same week. Every run also writes the week so far (the latest copy against the live summary). The Weekly report page
(site/report/) reads that file.

Nothing is recomputed from trades here: the weekly figures come only from each page's own published totals, so they
can't disagree with the pages. When a page's record shrank during a week (old records cleaned up by a later rule, for
example D92), that week's figure for it is left blank rather than guessed.
Standard library only.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get("OMEGA_STATE_DIR") or ROOT / "state")
KEEP_WEEKS = 104
FIELDS = ("key", "name", "path", "n", "avg", "random", "status", "unit", "equity", "open", "paused")


def week_of(t: datetime) -> str:
    y, w, _ = t.isocalendar()
    return f"{y}-W{w:02d}"


def week_start(t: datetime) -> datetime:
    d = t.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return d - timedelta(days=d.weekday())


def snapshot(summary: dict, now: datetime) -> dict:
    return {"week": week_of(now), "starts": week_start(now).isoformat(), "taken_at": now.isoformat(),
            "sections": [{k: s.get(k) for k in FIELDS} for s in summary.get("sections", [])]}


def week_figures(a: dict, b: dict) -> dict:
    """What happened between two copies of one section's record: trades finished, and their average vs random."""
    out = {"key": b["key"], "name": b["name"], "path": b["path"], "unit": b.get("unit", "%"), "status": b.get("status"),
           "n_total": b.get("n"), "trades": None, "avg": None, "random": None}
    if b.get("equity") is not None or a.get("equity") is not None:
        out["equity_from"], out["equity_to"] = a.get("equity"), b.get("equity")
    na, nb = a.get("n") or 0, b.get("n") or 0
    if nb < na:                      # the record was cleaned up during the week: no honest weekly figure
        return out
    out["trades"] = nb - na
    if nb > na:
        for k in ("avg", "random"):
            va, vb = a.get(k), b.get(k)
            if vb is not None and (va is not None or na == 0):
                out[k] = (vb * nb - (va or 0) * na) / (nb - na)
    return out


def weeks_between(snaps: list[dict], live: dict | None = None) -> list[dict]:
    """One report per finished week (newest first), plus the week so far when the live summary is given."""
    pairs = [(snaps[i], snaps[i + 1], False) for i in range(len(snaps) - 1)]
    if live is not None and snaps:
        pairs.append((snaps[-1], live, True))
    out = []
    for a, b, so_far in reversed(pairs):
        before = {s["key"]: s for s in a["sections"]}
        rows = [week_figures(before.get(s["key"], {}), s) for s in b["sections"]]
        out.append({"week": a["week"], "starts": a["starts"], "from": a["taken_at"],
                    "to": b.get("taken_at") or b.get("generated_at"), "so_far": so_far, "sections": rows})
    return out


def update(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    path = STATE / "reports" / "weekly.json"
    try:
        summary = json.loads((STATE / "summary.json").read_text())
    except (OSError, ValueError):
        print("report: no summary yet")
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {"snapshots": []}
    snaps = data["snapshots"]
    if not snaps or snaps[-1]["week"] != week_of(now):
        snaps.append(snapshot(summary, now))
        del snaps[:-KEEP_WEEKS]
        print(f"report: froze the record for {week_of(now)} ({len(snaps)} weeks kept)")
    data["weeks"] = weeks_between(snaps)
    data["so_far"] = weeks_between(snaps[-1:], summary)[0]
    data["generated_at"] = now.isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, default=str))
    return data


if __name__ == "__main__":
    update()
