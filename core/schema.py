"""Canonical column names for the per-symbol bar frame.

Every data source (live or synthetic) is normalised into ONE pandas DataFrame
per symbol, indexed by bar-open time (UTC). Missing sources simply leave NaN
columns; LightGBM handles NaN natively and the ASI layer lowers trust.
"""
from __future__ import annotations

OHLCV = ["open", "high", "low", "close", "volume", "taker_buy_volume", "n_trades"]
CROSS_VENUE = ["close_coinbase", "close_kraken", "close_okx"]
DERIVATIVES = ["funding_rate", "mark_price", "open_interest", "liq_long_qty", "liq_short_qty", "liq_count",
               "minutes_to_funding"]
BOOK = ["bid_depth", "ask_depth", "book_ofi", "depth_persist", "cancel_rate", "iceberg_score", "spread_bps"]
ONCHAIN = ["dex_buy_intent", "dex_sell_intent", "base_fee_gwei", "priority_fee_gwei", "stable_mint",
           "stable_burn", "stable_exch_inflow", "stable_exch_outflow", "btc_mempool_vsize", "btc_fee_fast"]
ALL_RAW = OHLCV + CROSS_VENUE + DERIVATIVES + BOOK + ONCHAIN

# Which raw columns come from which independent source family (used by ASI cross-source confirmation)
SOURCE_OF = {
    **{c: "cex_primary" for c in OHLCV},
    "close_coinbase": "coinbase", "close_kraken": "kraken", "close_okx": "okx",
    **{c: "derivs" for c in DERIVATIVES},
    **{c: "book" for c in BOOK},
    "dex_buy_intent": "eth_mempool", "dex_sell_intent": "eth_mempool",
    "base_fee_gwei": "eth_chain", "priority_fee_gwei": "eth_chain",
    "stable_mint": "eth_chain", "stable_burn": "eth_chain",
    "stable_exch_inflow": "eth_chain", "stable_exch_outflow": "eth_chain",
    "btc_mempool_vsize": "btc_mempool", "btc_fee_fast": "btc_mempool",
}


def ensure_columns(df):
    """Add any missing canonical columns as NaN (in place) and return df."""
    import numpy as np

    for c in ALL_RAW:
        if c not in df.columns:
            df[c] = np.nan
    return df
