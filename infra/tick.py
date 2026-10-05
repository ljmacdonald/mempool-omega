"""Start every job that is due. Run by .github/workflows/tick.yml, which an outside timer (cron-job.org, see
SCHEDULER.md) starts every 15 minutes, because GitHub's own timer has been skipping most scheduled runs.

The jobs keep their own GitHub schedules too; this only fills the gaps. A job is started when its last run is older
than its interval (minus a few minutes of slack) and none is already queued or running. Nightly tasks are started
once after their time if they haven't run since. Uses only the workflow's own GITHUB_TOKEN (actions: write).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

SLACK_MIN = 4


@dataclass
class Job:
    file: str
    task: str | None            # value for the workflow's `task` input (None: no input)
    every_min: int = 0          # regular job: start when the last run is this old
    nightly: str | None = None  # "HH:MM" UTC: start once a day after this time
    marks: tuple = ()           # words in a run's title that identify this nightly task
    window: str = "always"      # always | fx | us_stocks
    weekdays_only: bool = False


JOBS = [
    Job("paper.yml", None, every_min=60),
    Job("retrain.yml", None, nightly="02:17"),
    Job("dex.yml", "hourly", every_min=60, marks=()),
    Job("dex.yml", "train", nightly="03:41", marks=("train", "41 3 * * *")),
    Job("fx.yml", "scan", every_min=15, window="fx"),
    Job("fx.yml", "train", nightly="22:25", marks=("train", "25 22 * * 1-5"), weekdays_only=True),
    Job("stocks.yml", "scan", every_min=15, window="us_stocks"),
    Job("stocks.yml", "train", nightly="21:50", marks=("train", "50 21 * * 1-5"), weekdays_only=True),
    Job("ict.yml", "scan", every_min=15),
    Job("ict.yml", "backtest", nightly="04:50", marks=("backtest", "50 4 * * *")),
    Job("lab.yml", "scan", every_min=15),
    Job("lab.yml", "backtest", nightly="05:20", marks=("backtest", "20 5 * * *")),
]
NIGHTLY_MARKS = {j.file: j.marks for j in JOBS if j.nightly and j.marks}


def in_window(window: str, now: datetime) -> bool:
    wd, mins = now.weekday(), now.hour * 60 + now.minute          # Monday = 0, UTC
    if window == "fx":                                              # Sunday 21:00 UTC to Friday 21:00 UTC
        return wd < 4 or (wd == 4 and mins < 21 * 60) or (wd == 6 and mins >= 21 * 60)
    if window == "us_stocks":                                       # covers 9:30-16:00 New York, summer and winter
        return wd < 5 and 13 * 60 <= mins < 21 * 60
    return True


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def is_nightly_run(file: str, title: str) -> bool:
    return any(m in (title or "") for m in NIGHTLY_MARKS.get(file, ()))


def due(job: Job, runs: list[dict], now: datetime) -> bool:
    """runs: this workflow's recent runs, newest first, with created_at, status, display_title."""
    if any(r.get("status") in ("queued", "in_progress", "waiting", "pending", "requested") for r in runs):
        return False
    if job.nightly:
        if job.weekdays_only and now.weekday() > 4:
            return False
        hh, mm = map(int, job.nightly.split(":"))
        t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now < t or now - t > timedelta(hours=6):
            return False
        mine = [r for r in runs if (is_nightly_run(job.file, r.get("display_title", "")) if job.marks else True)]
        return not any(_ts(r["created_at"]) >= t - timedelta(minutes=10) for r in mine)
    if not in_window(job.window, now):
        return False
    regular = [r for r in runs if not is_nightly_run(job.file, r.get("display_title", ""))]
    if not regular:
        return True
    return now - _ts(regular[0]["created_at"]) >= timedelta(minutes=job.every_min - SLACK_MIN)


def _api(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                                          **({"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"}
                                             if os.environ.get("GITHUB_TOKEN") else {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def main() -> int:
    now = datetime.now(timezone.utc)
    ref = os.environ.get("GITHUB_REF_NAME", "main")
    cache: dict[str, list] = {}
    started = []
    for job in JOBS:
        try:
            if job.file not in cache:
                cache[job.file] = _api("GET", f"/actions/workflows/{job.file}/runs?per_page=20").get("workflow_runs", [])
            if due(job, cache[job.file], now):
                if os.environ.get("TICK_DRY"):          # show what would start, start nothing
                    started.append(f"(dry run) {job.file} {job.task or ''}".strip())
                    continue
                _api("POST", f"/actions/workflows/{job.file}/dispatches",
                     {"ref": ref, **({"inputs": {"task": job.task}} if job.task else {})})
                started.append(f"{job.file} {job.task or ''}".strip())
                cache[job.file] = [{"status": "queued", "created_at": now.isoformat(), "display_title": job.task or ""}] + cache[job.file]
        except Exception as e:  # noqa: BLE001
            print(f"{job.file} {job.task}: {e}", file=sys.stderr)
    print("started: " + (", ".join(started) if started else "nothing was due"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
