import pandas as pd
import pytest

from core.config import get_settings
from omega.model import Calibration
from paper import ledger
from paper.engine import TradingEngine
from paper.testnet import BinanceSpotTestnet, TestnetDisabled


def test_live_trading_is_impossible():
    assert get_settings().live_trading is False


def test_testnet_refuses_by_default_and_mainnet():
    with pytest.raises(TestnetDisabled):
        BinanceSpotTestnet()
    with pytest.raises(TestnetDisabled):
        BinanceSpotTestnet(base_url="https://api.binance.com")


def test_engine_entry_fill_and_exit_cycle(tmp_path):
    s = get_settings()
    eng = TradingEngine(s, Calibration(1.2, 1.0), seed=0)
    t0 = pd.Timestamp("2026-01-01 00:00", tz="UTC")
    bar = {"open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0}
    strong = {"p_up": 0.8, "side": 1, "meta_p": 0.8, "signal_trust": 0.9}
    eng.on_bar("BTCUSDT", t0, bar, strong, 0.004, 1.0, 1e7)
    assert "BTCUSDT" in eng.state.pending
    eng.on_bar("BTCUSDT", t0 + pd.Timedelta(minutes=5), bar, strong, 0.004, 1.0, 1e7, allow_entry=False)
    assert "BTCUSDT" in eng.state.positions
    crash = {"open": 99.0, "high": 99.0, "low": 90.0, "close": 91.0}
    eng.on_bar("BTCUSDT", t0 + pd.Timedelta(minutes=10), crash, strong, 0.004, 1.0, 1e7, allow_entry=False)
    assert "BTCUSDT" not in eng.state.positions
    assert eng.trades[-1]["exit_reason"] == "stop_loss" and eng.trades[-1]["net_pnl"] < 0
    eng.save(tmp_path / "s.json")
    st = TradingEngine.load_state(tmp_path / "s.json")
    assert st.cash == pytest.approx(eng.state.cash)


def test_ledger_roundtrip():
    n = ledger.append("events", [{"ts": "2026-01-01T00:00:00Z", "kind": "info", "x": 1}])
    assert n == 1
    assert len(ledger.read("events")) >= 1


def test_paper_run_once_synthetic():
    from paper.trader import run_once

    st = run_once(mode="synthetic")
    assert st["equity"] > 0 and set(st["symbols"]) == set(get_settings().symbols)
