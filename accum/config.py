"""Settings for the Accumulation lab (D119). Every threshold here is a provisional research setting, chosen before any
forward result existed; changing one bumps SCORE_VERSION so earlier results stay comparable to their own rules."""
from __future__ import annotations

SCORE_VERSION = 1
RECOVERY_VERSION = 1

# ---- universe (Strategy A): DEX tokens with enough liquidity to matter, old enough to have a history
CHAINS = {"solana": {"gt": "solana", "label": "Solana"}, "eth": {"gt": "eth", "label": "Ethereum"}}
POOL_PAGES = 10                        # GeckoTerminal top pools by 24 h volume, 20 per page
MAX_TOKENS = 30                        # per chain (the free quote limits set this, see DATA_PACE_S)
MIN_RESERVE_USD = 250_000
MAX_RESERVE_USD = 80_000_000           # the very largest tokens trade too much to see who is buying from 300 trades
MIN_POOL_AGE_D = 30                    # brand-new launches belong to the Sniper lab
STABLE_OR_WRAPPED = {"USDC", "USDT", "DAI", "USDE", "USDS", "PYUSD", "FDUSD", "TUSD", "USD1", "USDG", "FRAX", "LUSD",
                     "SOL", "WSOL", "ETH", "WETH", "STETH", "WSTETH", "CBETH", "RETH", "WEETH", "EZETH", "WBTC", "CBBTC",
                     "TBTC", "JITOSOL", "MSOL", "BSOL", "JUPSOL", "INF", "EURC", "PAXG", "XAUT", "SUSDE", "BUIDL"}

# ---- evidence
MIN_TRADE_USD = 500                    # only swaps of $500+ are collected (fewer, more meaningful trades per request)
BOT_TRADES_PER_DAY = 30                # a wallet trading a token more often than this in a day is treated as a bot
ROUND_TRIP_SHARE = 0.2                 # a wallet whose daily net is under 20% of what it traded is round-tripping
WALLET_KEEP_USD = 1_000                # per-wallet daily rows are kept when the wallet's net that day is at least this
RAW_KEEP_D = 3                         # raw swaps kept (evidence references, duplicate checks)
DAILY_KEEP_D = 100
WINDOWS = (7, 14, 30, 60, 90)
TOP_WALLETS = 5                        # balances looked up for the biggest net buyers of each candidate

# ---- admission routes (first qualification is frozen with its evidence)
ROUTE_A = {"window": 7, "min_share": 0.0010, "min_usd": 25_000, "min_buy_days": 4, "min_coverage": 0.8}
ROUTE_B = {"window": 14, "min_share": 0.0005, "min_usd": 10_000, "min_multiple": 2.0}
ROUTE_C = {"window": 14, "min_wallets": 15, "min_share": 0.0005, "min_ratio": 1.3}
ROUTE_X = {"window": 14, "lift": 0.03, "min_quote_vol": 2_000_000, "price_band": (-0.15, 0.10)}   # exchange flow
REPRICING = 0.30                       # +30% in 14 days: already repricing (shown, never required)
WEAKENING = 0.25                       # last 7 days' net below 25% of the 7-day rate at detection
DISTRIBUTION = 0.5                     # the accumulating wallets sold half of what they had bought
ARCHIVE_D = 30
MIN_LIQUIDITY_KEEP = 100_000           # below this a candidate is disqualified (not executable)

# ---- score v1 (points) - provisional, see docs/ACCUMULATION.md
WEIGHTS = {"verified": 25, "buyer_relative": 20, "persistence": 15, "supply_share": 10, "liquidity": 10,
           "participation": 10, "price": 5, "quality": 5}

# ---- forward evaluation and the paper account
HORIZONS = (7, 14, 30, 60, 90)
PAPER_START = 100.0                    # USDC
RISK_PER_TRADE = 0.01
MAX_OPEN_RISK = 0.04
MAX_POSITION = 0.05
MIN_RR = 2.0
DEX_FEE = 0.003                        # typical pool fee each way
NETWORK_FEE_USD = {"solana": 0.01, "eth": 0.30}   # assumed per swap (Ethereum gas at ~1-2 gwei)
MAX_HOLD_D = 30
STOP_RANGE = (0.03, 0.25)              # an invalidation level 3-25% below entry, else no defensible stop
PRICE_STALE_MIN = 120
USDC_DEPEG = 0.01

# ---- distressed recovery (Strategy B)
REC_MIN_DRAWDOWN = 0.75
REC_MIN_MCAP = 10_000_000
REC_MIN_VOLUME = 500_000
REC_HISTORY_D = 730
REC_WEIGHTS = {"usage": 20, "supply": 20, "liquidity": 15, "catalysts": 15, "drawdown": 10, "stabilisation": 10,
               "quality": 10}
BT_COST = 0.002                        # round trip, Binance spot taker fees and spread
BT_HORIZONS = (30, 90, 180)

# ---- data sources
GT = "https://api.geckoterminal.com/api/v2"
SOL_RPC = "https://api.mainnet-beta.solana.com"
ETHERSCAN = "https://api.etherscan.io/v2/api"
BINANCE = "https://data-api.binance.vision/api/v3"
COINGECKO = "https://api.coingecko.com/api/v3"
LLAMA = "https://api.llama.fi"
GT_PACE_S = 2.2                        # GeckoTerminal free: 30 requests a minute

# Known exchange custody wallets (curated, public labels). Balances held here are customers' coins, not one investor's;
# transfers to them are a possible-sale warning, never proof of a sale.
EXCHANGE_WALLETS = {
    "eth": {"0x28c6c06298d514db089934071355e5743bf21d60": "Binance", "0x21a31ee1afc51d94c2efccaa2092ad1028285549": "Binance",
            "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": "Binance", "0x71660c4005ba85c37ccec55d0c4493e66fe775d3": "Coinbase",
            "0x503828976d22510aad0201ac7ec88293211d23da": "Coinbase", "0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43": "Coinbase",
            "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "OKX", "0x2910543af39aba0cd09dbb2d50200b3e800a63d2": "Kraken",
            "0xf89d7b9c864f589bbf53a82105107622b35eaa40": "Bybit"},
    "solana": {"9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM": "Binance", "H8sMJSCQxfKiFTCfDR3DUMLPwcRbM61LGFJ8N4dK3WjS": "Coinbase"},
}
