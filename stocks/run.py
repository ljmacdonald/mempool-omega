"""US stocks entry points (paper / education only).

  python -m stocks.run scan     # every 30 min while the market is open: snapshot + hourly track record
  python -m stocks.run train    # nightly after the close: swing stats, earnings, models
"""
from __future__ import annotations

import argparse
import json

from core.log import get_logger

log = get_logger("stocks.run")


def main() -> None:
    ap = argparse.ArgumentParser(description="US stocks scanner (paper / education only)")
    ap.add_argument("cmd", choices=["scan", "train"])
    ap.add_argument("--track", action="store_true", help="update the track record now")
    a = ap.parse_args()
    from stocks.live import load_models

    if a.cmd == "train" or len(load_models()) < 2:
        from stocks.improve import train

        print(json.dumps(train(), indent=1, default=str))
    if a.cmd == "scan":
        from stocks.live import run

        p = run(True if a.track else None)
        print(json.dumps({lk: {k: [d["symbol"] for d in v[:5]] for k, v in by.items()} for lk, by in p["lists"].items()}))


if __name__ == "__main__":
    main()
