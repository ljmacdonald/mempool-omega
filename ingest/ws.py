"""Time-boxed public WebSocket capture.

GitHub Actions jobs are batch jobs, so instead of a forever-running daemon we
open public streams for a bounded window (default 45s) during each run and
summarise them into book/flow microstructure features.

Streams: Binance ``depth20@100ms``, ``aggTrade``, ``forceOrder`` (futures),
``markPrice``; OKX ``books5`` + ``trades``; Coinbase ``level2_batch`` is
authenticated now, so we use ``ticker`` + ``matches``.
Requires the optional ``websockets`` package; if missing or blocked, callers
fall back to REST snapshots.
"""
from __future__ import annotations

import asyncio
import json
import time

from core.log import get_logger
from ingest.cex import venue_symbol

log = get_logger("ingest.ws")

BINANCE_SPOT_WS = "wss://stream.binance.com:9443/stream?streams="
BINANCE_FUT_WS = "wss://fstream.binance.com/stream?streams="
OKX_WS = "wss://ws.okx.com:8443/ws/v5/public"


def binance_streams(symbol: str) -> tuple[str, str]:
    s = venue_symbol(symbol, "binance").lower()
    spot = BINANCE_SPOT_WS + "/".join([f"{s}@depth20@100ms", f"{s}@aggTrade"])
    fut = BINANCE_FUT_WS + "/".join([f"{s}@forceOrder", f"{s}@markPrice@1s"])
    return spot, fut


async def _capture(url: str, seconds: float, subscribe: dict | None = None, max_msgs: int = 20000) -> list[dict]:
    import websockets  # optional dependency

    msgs: list[dict] = []
    deadline = time.monotonic() + seconds
    async with websockets.connect(url, open_timeout=10, ping_interval=15, max_size=2**22) as ws:
        if subscribe:
            await ws.send(json.dumps(subscribe))
        while time.monotonic() < deadline and len(msgs) < max_msgs:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                break
            try:
                msgs.append(json.loads(raw))
            except ValueError:
                continue
    return msgs


def capture_binance(symbol: str, seconds: float = 45.0) -> dict:
    """Returns {'books': [...], 'trades': [...], 'liqs': [...], 'marks': [...]}."""
    spot, fut = binance_streams(symbol)

    async def run():
        return await asyncio.gather(_capture(spot, seconds), _capture(fut, seconds), return_exceptions=True)

    res = asyncio.run(run())
    out = {"books": [], "trades": [], "liqs": [], "marks": []}
    for r in res:
        if isinstance(r, Exception):
            log.warning("binance ws failed: %s", r)
            continue
        for m in r:
            stream, data = m.get("stream", ""), m.get("data", {})
            if "depth20" in stream:
                out["books"].append({"ts": time.time(),
                                     "bids": [(float(p), float(q)) for p, q in data.get("bids", [])],
                                     "asks": [(float(p), float(q)) for p, q in data.get("asks", [])]})
            elif "aggTrade" in stream:
                out["trades"].append({"ts": data["T"] / 1000, "price": float(data["p"]), "qty": float(data["q"]),
                                      "side": -1 if data["m"] else 1})
            elif "forceOrder" in stream:
                o = data.get("o", {})
                out["liqs"].append({"ts": o.get("T", 0) / 1000, "side": o.get("S"), "qty": float(o.get("q", 0))})
            elif "markPrice" in stream:
                out["marks"].append({"ts": data["E"] / 1000, "mark": float(data["p"]), "funding": float(data["r"])})
    return out


def capture_okx(symbol: str, seconds: float = 45.0) -> dict:
    inst = venue_symbol(symbol, "okx")
    sub = {"op": "subscribe", "args": [{"channel": "books5", "instId": inst}, {"channel": "trades", "instId": inst}]}
    try:
        msgs = asyncio.run(_capture(OKX_WS, seconds, subscribe=sub))
    except Exception as e:  # noqa: BLE001
        log.warning("okx ws failed: %s", e)
        return {"books": [], "trades": []}
    out = {"books": [], "trades": []}
    for m in msgs:
        ch = m.get("arg", {}).get("channel")
        for d in m.get("data", []) or []:
            if ch == "books5":
                out["books"].append({"ts": int(d["ts"]) / 1000,
                                     "bids": [(float(b[0]), float(b[1])) for b in d["bids"]],
                                     "asks": [(float(a[0]), float(a[1])) for a in d["asks"]]})
            elif ch == "trades":
                out["trades"].append({"ts": int(d["ts"]) / 1000, "price": float(d["px"]), "qty": float(d["sz"]),
                                      "side": 1 if d["side"] == "buy" else -1})
    return out


def capture_any(symbol: str, seconds: float = 45.0) -> dict:
    """Try Binance, then OKX. Returns dict with 'books', 'trades', 'venue'."""
    try:
        import websockets  # noqa: F401
    except ImportError:
        log.info("websockets not installed; skipping stream capture")
        return {"books": [], "trades": [], "venue": None}
    try:
        b = capture_binance(symbol, seconds)
        if b["books"]:
            return {**b, "venue": "binance"}
    except Exception as e:  # noqa: BLE001
        log.warning("binance capture error: %s", e)
    o = capture_okx(symbol, seconds)
    return {**o, "venue": "okx" if o["books"] else None}
