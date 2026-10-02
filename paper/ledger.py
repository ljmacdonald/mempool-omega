"""Append-only ledger committed back to the repo: Parquet (analysis) + CSV (human-readable).

Files are partitioned by month to keep each commit small:
  state/ledger/trades_YYYY-MM.{parquet,csv}
  state/ledger/signals_YYYY-MM.{parquet,csv}
  state/ledger/events_YYYY-MM.{parquet,csv}
  state/ledger/equity_YYYY-MM.{parquet,csv}
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.config import state_path

KINDS = ("trades", "signals", "events", "equity")


def _paths(kind: str, month: str) -> tuple[Path, Path]:
    base = state_path("ledger", f"{kind}_{month}.csv")
    return base.with_suffix(".parquet"), base


def append(kind: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    assert kind in KINDS, kind
    df = pd.DataFrame(rows)
    ts_col = "exit_ts" if kind == "trades" else "ts"
    df["_month"] = pd.to_datetime(df[ts_col], utc=True).dt.strftime("%Y-%m")
    for month, part in df.groupby("_month"):
        part = part.drop(columns="_month")
        pq, csv = _paths(kind, month)
        if pq.exists():
            old = pd.read_parquet(pq)
            part = pd.concat([old, part], ignore_index=True)
        for c in part.columns:
            if part[c].dtype == object:
                part[c] = part[c].astype(str)
        part.to_parquet(pq, index=False)
        part.to_csv(csv, index=False)
    return len(df)


def read(kind: str) -> pd.DataFrame:
    files = sorted(Path(state_path("ledger", "x").parent).glob(f"{kind}_*.parquet"))
    if not files:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
