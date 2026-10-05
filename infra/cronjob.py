"""Look after the cron-job.org timer that starts the Tick job (see SCHEDULER.md), using the CRONJOB_API_KEY secret.

  python infra/cronjob.py status                 # list the jobs, their schedule, last result and next run
  python infra/cronjob.py schedule 1,16,31,46    # run at these minutes of every hour (UTC)
  python infra/cronjob.py pause | resume

Never prints the API key or a job's headers (the Tick job's headers hold the GitHub token).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.request

API = "https://api.cron-job.org"
TITLE = "Mempool Omega tick"


def call(method: str, path: str, body: dict | None = None) -> dict:
    key = os.environ.get("CRONJOB_API_KEY", "").strip()
    if not key:
        raise SystemExit("CRONJOB_API_KEY is not set: add it under Settings -> Secrets and variables -> Actions.")
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def jobs() -> list[dict]:
    return call("GET", "/jobs").get("jobs", [])


def tick_job() -> dict:
    for j in jobs():
        if j.get("title") == TITLE:
            return j
    raise SystemExit(f'No cron-job.org job called "{TITLE}" was found.')


def when(ts) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ts else "-"


def status() -> None:
    for j in jobs():
        s = j.get("schedule", {})
        print(f'{j.get("title")}: enabled={j.get("enabled")} minutes={s.get("minutes")} hours={s.get("hours")} '
              f'timezone={s.get("timezone")} last run={when(j.get("lastExecution"))} last HTTP status={j.get("lastStatus")} '
              f'next run={when(j.get("nextExecution"))}')


def parse_minutes(text: str) -> list[int]:
    mins = sorted({int(x) for x in text.replace(" ", "").split(",") if x != ""})
    if not mins or any(m < 0 or m > 59 for m in mins):
        raise SystemExit("Minutes must be numbers from 0 to 59, separated by commas, e.g. 1,16,31,46")
    return mins


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        status()
        return 0
    j = tick_job()
    if cmd == "schedule":
        mins = parse_minutes(argv[1] if len(argv) > 1 else "1,16,31,46")
        call("PATCH", f"/jobs/{j['jobId']}", {"job": {"schedule": {"timezone": "UTC", "expiresAt": 0, "hours": [-1], "mdays": [-1],
                                                                   "months": [-1], "wdays": [-1], "minutes": mins}}})
    elif cmd in ("pause", "resume"):
        call("PATCH", f"/jobs/{j['jobId']}", {"job": {"enabled": cmd == "resume"}})
    else:
        print(__doc__)
        return 2
    status()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
