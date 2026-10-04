"""Trading speeds ("styles"). Each has its own candle size, holding time limit and model.

               candles   exit at the latest   typical move size used for exits
  quick        5 min     1 hour               0.6 % - 5 %
  short        15 min    4 hours              1 %   - 8 %
  day          1 hour    24 hours             1.5 % - 12 %
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Style:
    key: str
    label: str
    interval: str          # Binance kline interval
    bar_minutes: int
    horizon_bars: int      # time limit, in candles
    min_risk: float
    max_risk: float
    train_bars: int        # candles per coin used for nightly training
    live_bars: int = 240   # candles per coin needed to score the latest bar

    @property
    def horizon_minutes(self) -> int:
        return self.bar_minutes * self.horizon_bars

    @property
    def hold_text(self) -> str:
        return human_duration(self.horizon_minutes)

    def bars_text(self, n_bars: int) -> str:
        return human_duration(n_bars * self.bar_minutes)


def human_duration(minutes: float) -> str:
    minutes = int(round(minutes))
    if minutes < 60:
        return f"{minutes} minutes"
    h = minutes / 60
    if h < 48:
        return f"{h:g} hour" + ("" if h == 1 else "s")
    return f"{h / 24:g} days"


STYLES: dict[str, Style] = {
    "quick": Style("quick", "Quick: exit within 1 hour", "5m", 5, 12, 0.006, 0.05, 2000),
    "short": Style("short", "Short: exit within 4 hours", "15m", 15, 16, 0.010, 0.08, 1500),
    "day": Style("day", "Day: exit within 24 hours", "1h", 60, 24, 0.015, 0.12, 1000),
}
DEFAULT_STYLE = "short"

# DEX tokens (dex/): hourly candles from GeckoTerminal; bigger swings, so wider exits.
DEX_STYLES: dict[str, Style] = {
    "dex_short": Style("dex_short", "Short: exit within 4 hours", "1h", 60, 4, 0.02, 0.15, 1000),
    "dex_day": Style("dex_day", "Day: exit within 24 hours", "1h", 60, 24, 0.04, 0.30, 1000),
}
DEX_DEFAULT_STYLE = "dex_short"


# US stocks (stocks/): Yahoo candles, regular trading hours only.
STOCK_STYLES: dict[str, Style] = {
    "stk_today": Style("stk_today", "Today: sell within 2 hours, before the close", "15m", 15, 8, 0.004, 0.04, 1500),
    "stk_days": Style("stk_days", "Few days: sell within 3 trading days", "1h", 60, 21, 0.01, 0.10, 1400),
}


# Forex (fx/): Yahoo candles, 24 hours a day Monday-Friday. Each pair is ranked both ways (buy or sell).
FX_STYLES: dict[str, Style] = {
    "fx_today": Style("fx_today", "Today: close within 4 hours", "15m", 15, 16, 0.0015, 0.02, 1500),
    "fx_days": Style("fx_days", "Few days: close within 3 days", "1h", 60, 72, 0.004, 0.05, 2000),
}


def get_style(key: str) -> Style:
    return STYLES.get(key) or DEX_STYLES.get(key) or STOCK_STYLES.get(key) or FX_STYLES[key]
