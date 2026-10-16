"""Settings for the MEV & transaction survival engine (D103). Every address here was checked against a live source on
14 Oct 2026: token mints by a Jupiter quote (USDC from Jupiter's token search), Jito tip accounts by the block
engine's getTipAccounts."""
from __future__ import annotations

SOL = "So11111111111111111111111111111111111111112"
LAMPORTS = 1_000_000_000

# Token allowlist: liquid tokens only. Anything else is refused by the safety checker.
TOKENS = {
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "WIF": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "JTO": "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL",
    "RAY": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
    "PYTH": "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
    "mSOL": "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",
    "jitoSOL": "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",
}
MINTS = {SOL, *TOKENS.values()}

# Venues compared for cross-venue round trips (Jupiter route labels).
DEXES = ("Raydium CLMM", "Whirlpool", "Meteora DLMM")
TRADE_LAMPORTS = 1 * LAMPORTS          # every round trip is sized at 1 SOL
SLIPPAGE_BPS = 50

# Re-check delays after discovery (seconds). The free quote service allows about one request a second, so the
# fastest re-check is ~1 s; professional searchers act within one slot (~0.4 s). The page says so.
DELAYS_S = (1, 3, 10, 30)
SESSION_S = 8 * 60                     # one shadow session per hourly job
LEG_GAP_S = 0.15                       # the sell leg is quoted straight after its buy leg (then a longer pause)
MAX_LEG_GAP_S = 0.5                    # judged only if the two legs were quoted at most this far apart
METHOD_VERSION = 2                     # v1 (14 Oct) quoted the two legs seconds apart: kept, but not judged
QUOTE_PACE_S = 2.2                     # seconds between Jupiter requests: the free limit is 5 per 10 s (16 Oct)

# Network costs (lamports). Priority fee = compute-unit limit x price (micro-lamports per CU) / 1e6.
BASE_FEE = 5_000                       # per signature; one signer
CU_LIMIT = 600_000                     # conservative for a two-leg swap transaction
# Priority fees are read for busy SOL pools (getRecentPrioritizationFees reports the lowest fee that landed per slot,
# usually 0); the median of the non-zero ones is used, never below this assumed floor for a competitive trade.
FEE_ACCOUNTS = ["Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE", "8sjV1AqBFvFuADBCQHhotaRq5DFFYSjjg1jMyVWMqXvZ"]
PRIORITY_FLOOR = 10_000                # micro-lamports per compute unit (an assumption, shown as such on the page)
MIN_PROFIT = 10_000                    # an opportunity must clear every cost by at least this (lamports)
JITO_MIN_TIP = 1_000                   # Jito's documented minimum tip
JITO_TIP_SPLIT = 0.3                   # sendTransaction via Jito: 70/30 priority fee / tip split (Jito docs)

# Jito tip accounts (getTipAccounts; the docs say they "have remained constant").
JITO_TIP_ACCOUNTS = frozenset({
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe", "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49", "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL", "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY", "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
})

# Programs a transaction may touch. Anything else fails closed.
PROGRAMS = {
    "ComputeBudget111111111111111111111111111111": "Compute budget",
    "11111111111111111111111111111111": "System (tip transfers only)",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA": "Token",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb": "Token-2022",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL": "Associated token account",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "Jupiter aggregator v6",
}
DONTFRONT_PREFIX = "jitodontfront"

# Hard limits that hold whatever the model says (G: bankroll and loss limits stay independent).
MAX_INPUT_LAMPORTS = 1 * LAMPORTS
MAX_TIP_LAMPORTS = 2_000_000           # 0.002 SOL
MAX_CU_PRICE = 2_000_000               # micro-lamports per compute unit
MIN_SOL_RESERVE = 50_000_000           # 0.05 SOL always left in the wallet
DAILY_LOSS_LAMPORTS = 50_000_000       # 0.05 SOL a day, then stop

# Evidence needed before any verdict other than INSUFFICIENT_EVIDENCE.
MIN_CANDIDATES = 30                    # opportunities with a measured re-check
MIN_TEST = 10                          # of which in the later (validation) part

JUPITER = "https://lite-api.jup.ag/swap/v1"
RPC = "https://api.mainnet-beta.solana.com"
JITO_BLOCK_ENGINE = "https://mainnet.block-engine.jito.wtf"
JITO_TIP_FLOOR = "https://bundles.jito.wtf/api/v1/bundles/tip_floor"
