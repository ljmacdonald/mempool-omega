"""OPTIONAL Binance Spot TESTNET adapter - disabled by default.

Hard guards:
  * only https://testnet.binance.vision is allowed (mainnet URLs are refused)
  * requires ENABLE_TESTNET=1 and testnet API keys (create them at testnet.binance.vision;
    testnet keys hold fake funds and have no withdrawal capability)
  * there is NO withdraw / transfer method in this module, by design
"""
from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import urlencode

import requests

from core.config import get_settings
from core.log import get_logger

log = get_logger("paper.testnet")
TESTNET_URL = "https://testnet.binance.vision"


class TestnetDisabled(RuntimeError):
    pass


class BinanceSpotTestnet:
    def __init__(self, base_url: str = TESTNET_URL):
        s = get_settings()
        if "testnet" not in base_url:
            raise TestnetDisabled("refusing non-testnet endpoint")
        if not s.enable_testnet:
            raise TestnetDisabled("ENABLE_TESTNET is not set")
        if not (s.testnet_key and s.testnet_secret):
            raise TestnetDisabled("BINANCE_TESTNET_API_KEY / _SECRET missing")
        self.base = base_url
        self.key, self._secret = s.testnet_key, s.testnet_secret.encode()

    def _signed(self, method: str, path: str, params: dict) -> dict:
        params = {**params, "timestamp": int(time.time() * 1000), "recvWindow": 5000}
        q = urlencode(params)
        sig = hmac.new(self._secret, q.encode(), hashlib.sha256).hexdigest()
        r = requests.request(method, f"{self.base}{path}?{q}&signature={sig}",
                             headers={"X-MBX-APIKEY": self.key}, timeout=10)
        r.raise_for_status()
        return r.json()

    def account(self) -> dict:
        return self._signed("GET", "/api/v3/account", {})

    def place_order(self, symbol: str, side: int, qty: float, price: float | None = None) -> dict:
        """Maker-first: LIMIT_MAKER if price given (rejected instead of crossing), else MARKET."""
        p = {"symbol": symbol, "side": "BUY" if side > 0 else "SELL", "quantity": f"{qty:.5f}"}
        if price is not None:
            p.update(type="LIMIT_MAKER", price=f"{price:.2f}")
        else:
            p.update(type="MARKET")
        return self._signed("POST", "/api/v3/order", p)

    def cancel_all(self, symbol: str) -> dict:
        return self._signed("DELETE", "/api/v3/openOrders", {"symbol": symbol})


def maybe_mirror(event: dict) -> None:
    """Mirror a paper entry/exit to the testnet with a tiny fixed size, if enabled. Never raises."""
    try:
        tn = BinanceSpotTestnet()
    except TestnetDisabled:
        return
    try:
        if event["kind"] == "entry":
            tn.place_order(event["symbol"], event["side"], qty=0.001, price=event["price"])
        elif event["kind"] in ("exit", "kill_switch"):
            tn.cancel_all(event.get("symbol", "BTCUSDT"))
    except Exception as e:  # noqa: BLE001
        log.warning("testnet mirror failed: %s", e)
