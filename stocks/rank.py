"""Stock-specific wording, warnings and costs (the model and exits are shared with the crypto scanner)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.styles import Style

SEC_FEE = 0.0000278          # SEC fee on sells ($27.80 per $1M sold)
LARGE_MIN_MCAP = 10e9
LARGE_N = 200
VOL_MIN_MCAP, VOL_MIN_PRICE, VOL_MIN_DOLLAR_VOL, VOL_N = 300e6, 3.0, 10e6, 150
MIN_SWING = 0.15             # rose 20%+ within a week in >= 15 % of the last ~250 trading days


def spread(dollar_vol: float) -> float:
    """Typical round-trip bid/ask cost for a small market order, by how much the stock trades."""
    return 0.0001 if dollar_vol >= 1e9 else 0.0003 if dollar_vol >= 100e6 else 0.0008 if dollar_vol >= 20e6 else 0.002


def cost_rt(dollar_vol: float, fx: float = 0.0) -> float:
    """Round trip: bid/ask spread + SEC fee on the sell + currency conversion both ways (commission-free broker)."""
    return spread(dollar_vol) + SEC_FEE + 2 * fx


def universes(scr: pd.DataFrame, swing: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Large: the most-traded US stocks worth $10B+. Volatile: $300M-$10B, $3+, $10M+/day, often swinging 20%+."""
    s = scr[scr["price"] >= 1].sort_values("dollar_vol", ascending=False)
    large = s[(s["mcap"] >= LARGE_MIN_MCAP) & (s["price"] >= 5)].head(LARGE_N)
    vol = s[(s["mcap"] >= VOL_MIN_MCAP) & (s["mcap"] < LARGE_MIN_MCAP) & (s["price"] >= VOL_MIN_PRICE)
            & (s["dollar_vol"] >= VOL_MIN_DOLLAR_VOL)].head(VOL_N)
    if swing:
        vol = vol[vol["symbol"].map(lambda x: (swing.get(x) or {}).get("up_pct") or 0) >= MIN_SWING]
    return large.reset_index(drop=True), vol.reset_index(drop=True)


def reasons(f: pd.Series, st: Style) -> list[str]:
    out: list[tuple[float, str]] = []
    if f["ema24_dist"] > 0 and f["ema72_dist"] > 0:
        out.append((0.8, f"Price is above its average of the last {st.bars_text(24)} and of the last "
                         f"{st.bars_text(72)}, so the trend is up."))
    if f["rel_strength_24b"] > 0.005:
        out.append((f["rel_strength_24b"] * 40, f"It did {np.expm1(f['rel_strength_24b']):.1%} better than the "
                                                  f"S&P 500 over the last {st.bars_text(24)}."))
    if f["volume_surge"] > 1.5:
        out.append((min(f["volume_surge"] / 3, 1.0), f"Trading is {f['volume_surge']:.1f}x busier than usual: "
                                                      "investors are paying attention to it."))
    if f["dist_low_168b"] < 0.03 and f["ret_4b"] > 0:
        out.append((0.6, f"It is bouncing up from near its lowest price of the last {st.bars_text(168)}."))
    if 45 <= f["rsi_14"] <= 65:
        out.append((0.3, "Momentum is healthy: not overheated, not collapsing."))
    if f["dist_high_168b"] > -0.02:
        out.append((0.5, f"It is close to its highest price of the last {st.bars_text(168)}. A breakout is possible."))
    out.sort(key=lambda x: -x[0])
    return [t for _, t in out[:3]] or ["The model sees a slightly better-than-usual pattern, with no single "
                                       "strong reason."]


def flags(f: pd.Series, price: float, st: Style, earnings_on: str | None, today: str) -> tuple[float, list[str]]:
    pen, warn = 0.0, []
    if f["ret_24b"] > np.log(1.12) or f["ret_1b"] > np.log(1.04):
        pen += 0.15
        warn.append(f"Already up {np.expm1(f['ret_24b']):.0%} in the last {st.bars_text(24)}. Buying right after a "
                    "jump often means buying the top.")
    if f["rsi_14"] > 80:
        pen += 0.05
        warn.append("Looks 'overheated' (RSI above 80). Pullbacks are common after this.")
    if price < 5:
        pen += 0.10
        warn.append("Price under $5 (a 'penny stock'): easier to push around and often more volatile.")
    if f["btc_ret_24b"] < np.log(0.98):
        warn.append(f"The S&P 500 fell more than 2% in the last {st.bars_text(24)}. Most stocks follow the market.")
    if earnings_on:
        day = earnings_on[:10]
        if st.key == "stk_days" or (day == today and "before the open" not in earnings_on):
            pen += 0.30 if st.key == "stk_days" else 0.0
            warn.append(f"Reports results on {earnings_on}. Prices often jump or drop 5-20% on results, in either "
                        "direction.")
    return pen, warn
