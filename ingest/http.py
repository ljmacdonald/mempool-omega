"""Polite HTTP client: per-host rate limiting, retries with backoff, timeouts.

Respecting public rate limits is a hard requirement - every adapter goes
through ``PoliteClient``.
"""
from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests

from core.log import get_logger

log = get_logger("ingest.http")

# Conservative minimum spacing between requests per host (seconds)
HOST_MIN_INTERVAL = {
    "data-api.binance.vision": 0.05,   # weight limit 6000/min; klines = 2
    "api.binance.com": 0.15,
    "fapi.binance.com": 0.15,
    "www.okx.com": 0.12,
    "api.exchange.coinbase.com": 0.15,
    "api.kraken.com": 1.0,
    "api.bybit.com": 0.1,
    "mempool.space": 0.5,
    "api.etherscan.io": 0.25,
    "ethereum-rpc.publicnode.com": 0.1,
    "api.mainnet-beta.solana.com": 0.25,
    "api.geckoterminal.com": 3.0,      # free tier: 30 calls/min, often shared IPs: stay well below
    "api.dexscreener.com": 0.25,       # 300/min for pair endpoints
    "api.gopluslabs.io": 1.0,
    "api.honeypot.is": 0.6,
    "api.rugcheck.xyz": 0.6,
}


class PoliteClient:
    def __init__(self, timeout: float = 10.0, retries: int = 3, user_agent: str = "mempool-omega/0.1 (research)"):
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        gap = HOST_MIN_INTERVAL.get(host, 0.2)
        with self._lock:
            wait = self._last.get(host, 0.0) + gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()

    def request(self, method: str, url: str, **kw: Any) -> Any:
        kw.setdefault("timeout", self.timeout)
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            self._throttle(url)
            try:
                r = self.session.request(method, url, **kw)
                if r.status_code == 429 or r.status_code == 418:
                    try:
                        hinted = float(r.headers.get("Retry-After") or 0)
                    except ValueError:
                        hinted = 0.0
                    backoff = max(hinted, 12.0 * (attempt + 1) if "geckoterminal" in url else 2 ** (attempt + 1))
                    log.warning("rate limited by %s, backing off %.1fs", urlparse(url).netloc, backoff)
                    time.sleep(min(backoff, 60))
                    continue
                if r.status_code in (451, 403):
                    raise GeoBlocked(f"{url} -> HTTP {r.status_code}")
                r.raise_for_status()
                return r.json()
            except GeoBlocked:
                raise
            except Exception as e:  # noqa: BLE001 - network is flaky; degrade gracefully
                last_exc = e
                time.sleep(0.5 * 2**attempt)
        raise SourceUnavailable(f"{method} {url} failed: {last_exc}")

    def get(self, url: str, params: dict | None = None, **kw: Any) -> Any:
        return self.request("GET", url, params=params, **kw)

    def post(self, url: str, json: Any = None, **kw: Any) -> Any:
        return self.request("POST", url, json=json, **kw)


class SourceUnavailable(RuntimeError):
    pass


class GeoBlocked(SourceUnavailable):
    pass


_default: PoliteClient | None = None


def client() -> PoliteClient:
    global _default
    if _default is None:
        _default = PoliteClient()
    return _default
