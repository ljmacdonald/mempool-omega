"""Central configuration.

Everything is read from environment variables (populated from GitHub Actions
secrets / variables at runtime). There is deliberately NO .env file support.

Safety invariant: ``Settings.live_trading`` is hard-wired to ``False``. The
only non-paper path is the optional exchange *testnet* adapter, which itself
refuses to talk to any mainnet endpoint.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.environ.get("OMEGA_STATE_DIR", REPO_ROOT / "state"))


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return default if v is None or v.strip() == "" else v.strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(_env(name, str(default))))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


@dataclass
class RiskConfig:
    starting_equity: float = 100_000.0
    risk_per_trade: float = 0.01          # max 1% of equity at risk (stop distance) per trade
    max_risk_per_trade: float = 0.02      # hard ceiling
    kelly_fraction: float = 0.25          # fractional Kelly
    vol_target_annual: float = 0.40       # portfolio volatility target
    max_gross_leverage: float = 1.5
    max_position_frac: float = 0.5        # max notional per symbol / equity
    max_correlated_positions: int = 2     # correlation cap (crypto majors ~ highly correlated)
    daily_dd_kill: float = 0.03           # flatten + halt if daily drawdown exceeds 3%
    max_consecutive_losses: int = 5
    min_trust: float = 0.45               # trust floor to enter
    trust_collapse: float = 0.30          # trust floor to stay in a position
    min_data_quality: float = 0.5


@dataclass
class ExitConfig:
    pt_mult: float = 1.5      # profit target = pt_mult * sigma(horizon)
    sl_mult: float = 1.0      # stop = sl_mult * sigma(horizon)
    time_stop_bars: int = 12  # 12 x 5m = 1 hour
    decay_threshold: float = -0.03  # exit if (p_up-0.5)*side drops below this (signal flipped, with hysteresis)


@dataclass
class CostConfig:
    maker_fee_bps: float = 1.0
    taker_fee_bps: float = 5.0
    base_slippage_bps: float = 1.0
    impact_coef: float = 10.0       # bps * sqrt(participation)
    latency_bars: int = 1           # decisions fill on the next bar
    maker_fill_prob: float = 0.6    # chance a passive order fills before falling back to taker


@dataclass
class Settings:
    symbols: list[str] = field(default_factory=lambda: _env("OMEGA_SYMBOLS", "BTCUSDT,ETHUSDT").split(","))
    bar: str = _env("OMEGA_BAR", "5m")
    history_bars: int = _env_int("OMEGA_HISTORY_BARS", 600)
    train_days: int = _env_int("OMEGA_TRAIN_DAYS", 30)
    data_mode: str = _env("OMEGA_DATA_MODE", "auto")  # auto | live | synthetic
    seed: int = _env_int("OMEGA_SEED", 7)
    risk: RiskConfig = field(default_factory=RiskConfig)
    exits: ExitConfig = field(default_factory=ExitConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    # Optional secrets (all may be empty -> graceful degradation)
    telegram_token: str = _env("TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = _env("TELEGRAM_CHAT_ID")
    etherscan_key: str = _env("ETHERSCAN_API_KEY")
    thegraph_key: str = _env("THEGRAPH_API_KEY")
    eth_rpc: str = _env("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")
    sol_rpc: str = _env("SOL_RPC_URL", "https://api.mainnet-beta.solana.com")
    enable_testnet: bool = _env_bool("ENABLE_TESTNET", False)
    testnet_key: str = _env("BINANCE_TESTNET_API_KEY")
    testnet_secret: str = _env("BINANCE_TESTNET_API_SECRET")

    @property
    def live_trading(self) -> bool:
        """Real-money trading is not implemented and can never be switched on."""
        return False

    @property
    def bar_minutes(self) -> int:
        unit = self.bar[-1]
        n = int(self.bar[:-1])
        return n * {"m": 1, "h": 60}[unit]

    @property
    def pandas_freq(self) -> str:
        return f"{self.bar_minutes}min"

    def __post_init__(self) -> None:
        self.symbols = [s.strip().upper() for s in self.symbols if s.strip()]
        r = self.risk
        r.starting_equity = _env_float("OMEGA_STARTING_EQUITY", r.starting_equity)
        r.risk_per_trade = min(_env_float("OMEGA_RISK_PER_TRADE", r.risk_per_trade), r.max_risk_per_trade)
        r.daily_dd_kill = _env_float("OMEGA_DAILY_DD_KILL", r.daily_dd_kill)


def get_settings() -> Settings:
    return Settings()


def state_path(*parts: str) -> Path:
    p = STATE_DIR.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p
