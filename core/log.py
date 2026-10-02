"""Logging: human-readable to stderr + JSON lines to state/logs/omega.log."""
from __future__ import annotations

import json
import logging
import sys
import time

from core.config import state_path

_CONFIGURED = False


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def get_logger(name: str = "omega") -> logging.Logger:
    global _CONFIGURED
    if not _CONFIGURED:
        root = logging.getLogger("omega")
        root.setLevel(logging.INFO)
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s", "%H:%M:%S"))
        root.addHandler(h)
        try:
            fh = logging.FileHandler(state_path("logs", "omega.log"))
            fh.setFormatter(_JsonFormatter())
            root.addHandler(fh)
        except OSError:
            pass
        root.propagate = False
        _CONFIGURED = True
    return logging.getLogger(name if name.startswith("omega") else f"omega.{name}")
