"""Stream capture venue order: OKX first on GitHub's US runners (Binance answers HTTP 451 there), Binance first elsewhere."""
import sys
import types

from ingest import ws


def _fake(calls, books):
    def f(symbol, seconds):
        calls.append(f.__name__)
        return {"books": books, "trades": []}
    return f


def test_okx_first_on_github_and_binance_never_tried_when_okx_works(monkeypatch):
    monkeypatch.setitem(sys.modules, "websockets", types.ModuleType("websockets"))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    calls = []
    okx, bn = _fake(calls, [{"ts": 1}]), _fake(calls, [{"ts": 1}])
    okx.__name__, bn.__name__ = "okx", "binance"
    monkeypatch.setattr(ws, "capture_okx", okx)
    monkeypatch.setattr(ws, "capture_binance", bn)
    assert ws.capture_any("BTCUSDT", 1)["venue"] == "okx" and calls == ["okx"]


def test_falls_back_and_keeps_binance_first_elsewhere(monkeypatch):
    monkeypatch.setitem(sys.modules, "websockets", types.ModuleType("websockets"))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    calls = []
    okx, bn = _fake(calls, []), _fake(calls, [{"ts": 1}])
    okx.__name__, bn.__name__ = "okx", "binance"
    monkeypatch.setattr(ws, "capture_okx", okx)
    monkeypatch.setattr(ws, "capture_binance", bn)
    assert ws.capture_any("BTCUSDT", 1)["venue"] == "binance" and calls == ["okx", "binance"]
    monkeypatch.delenv("GITHUB_ACTIONS")
    assert ws.venue_order() == ("binance", "okx")
