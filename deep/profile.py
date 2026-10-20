"""Loads Engine B's profile.yaml (its own thresholds, separate from Engine A's)."""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import yaml

PATH = Path(__file__).with_name("profile.yaml")


@lru_cache(maxsize=1)
def load() -> dict:
    return yaml.safe_load(PATH.read_text())


def fingerprint() -> str:
    return hashlib.sha256(PATH.read_bytes()).hexdigest()[:12]
