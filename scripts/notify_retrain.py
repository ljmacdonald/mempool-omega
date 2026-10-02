"""Send a short Telegram summary after the nightly retrain (no-op without secrets)."""
from __future__ import annotations

import json
import sys

from alerts.telegram import send
from core.config import state_path


def main() -> None:
    status = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    parts = [f"nightly retrain: {status}"]
    try:
        tr = json.loads(state_path("reports", "train_latest.json").read_text())
        for sym, info in tr.get("symbols", {}).items():
            parts.append(f"{sym}: OOS AUC {info.get('oos_auc', float('nan')):.3f} ({info.get('data_mode')})")
        bt = json.loads(state_path("reports", "backtest_latest.json").read_text())
        parts.append(f"backtest: ret {bt.get('total_return', 0):.2%}, sharpe {bt.get('sharpe', 0):.2f}, "
                     f"maxDD {bt.get('max_drawdown', 0):.2%}, trades {bt.get('n_trades', 0)}")
    except (OSError, ValueError):
        pass
    send("info" if status == "success" else "error", " | ".join(parts))


if __name__ == "__main__":
    main()
