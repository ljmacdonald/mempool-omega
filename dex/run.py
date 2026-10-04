"""DEX scanner entry points (paper / education only).

  python -m dex.run hourly     # find, check and rank DEX tokens; write state/dex/snapshot.json; track record
  python -m dex.run train      # nightly: retrain the DEX models + self-improvement
"""
from __future__ import annotations

import argparse
import json

from core.log import get_logger

log = get_logger("dex.run")


def main() -> None:
    ap = argparse.ArgumentParser(description="DEX token scanner (paper / education only)")
    ap.add_argument("cmd", choices=["hourly", "train"])
    a = ap.parse_args()
    if a.cmd == "train":
        from dex.improve import train

        print(json.dumps(train(), indent=1, default=str)[:4000])
    else:
        from dex.live import hourly, load_models

        if not load_models():
            from dex.improve import train

            log.warning("no DEX models yet - training first")
            train()
        out = hourly()
        print(json.dumps({k: [(d["coin"], d["chain"], d["score"]) for d in v] for k, v in out["ideas"].items()}))


if __name__ == "__main__":
    main()
