"""Forex entry points (paper / education only).

  python -m fx.run scan      # every 15 min while the forex market is open
  python -m fx.run train     # nightly
"""
from __future__ import annotations

import argparse
import json


def main() -> None:
    ap = argparse.ArgumentParser(description="Forex scanner (paper / education only)")
    ap.add_argument("cmd", choices=["scan", "train"])
    ap.add_argument("--track", action="store_true")
    a = ap.parse_args()
    from fx.live import load_models

    if a.cmd == "train" or len(load_models()) < 2:
        from fx.improve import train

        print(json.dumps(train(), indent=1, default=str))
    if a.cmd == "scan":
        from fx.live import run

        p = run(True if a.track else None)
        print(json.dumps({lk: {k: [f"{d['side']} {d['pair']}" for d in v[:5]] for k, v in by.items()}
                          for lk, by in p["lists"].items()}))


if __name__ == "__main__":
    main()
