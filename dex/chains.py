"""Per-network settings. Addresses: EVM compared lower-case, Solana as-is.

quotes: the only tokens accepted on the other side of a pool. A pool paired with anything else can show a
huge "liquidity" figure made of the scammer's own token (seen live: pools with $700M+ "liquidity" and zero
trades), so it is never counted.
mev:    typical sandwich (front-running) cost per swap without protection, as a fraction of the amount.
sniper: prior for how much bots front-run a published idea (price drift between our suggestion and a
        realistic fill). Re-learned nightly from the track record (dex/improve.py).
"""
from __future__ import annotations

CHAINS: dict[str, dict] = {
    "solana": {
        "name": "Solana", "gt": "solana", "ds": "solana", "goplus": "solana", "honeypot": None, "rugcheck": True,
        "quotes": {"So11111111111111111111111111111111111111112": "SOL",
                   "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
                   "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT"},
        "native": "SOL", "gas_usd": 0.03, "mev": 0.005, "sniper": 0.005,
        "protect": "a wallet with MEV protection (Jito-protected / private transactions) and a 1% slippage limit",
        "explorer": "https://solscan.io/token/{}",
    },
    "bsc": {
        "name": "BNB Chain", "gt": "bsc", "ds": "bsc", "goplus": "56", "honeypot": 56, "rugcheck": False,
        "quotes": {"0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": "WBNB",
                   "0x55d398326f99059ff775485246999027b3197955": "USDT",
                   "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": "USDC"},
        "native": "BNB", "gas_units": 180000, "rpc": "https://bsc-rpc.publicnode.com", "mev": 0.005, "sniper": 0.005,
        "protect": "a private RPC (for example bloXroute or 48 Club) and a 1% slippage limit",
        "explorer": "https://bscscan.com/token/{}",
    },
    "ethereum": {
        "name": "Ethereum", "gt": "eth", "ds": "ethereum", "goplus": "1", "honeypot": 1, "rugcheck": False,
        "quotes": {"0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH",
                   "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
                   "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
                   "0x6b175474e89094c44da98b954eedeac495271d0f": "DAI"},
        "native": "ETH", "gas_units": 180000, "rpc": "https://ethereum-rpc.publicnode.com", "mev": 0.005, "sniper": 0.005,
        "protect": "Flashbots Protect or MEV Blocker (free private RPCs) and a 1% slippage limit",
        "explorer": "https://etherscan.io/token/{}",
    },
    "robinhood": {
        "name": "Robinhood Chain", "gt": "robinhood", "ds": "robinhood", "goplus": "4663", "honeypot": None,
        "rugcheck": False,
        "quotes": {"0x0bd7d308f8e1639fab988df18a8011f41eacad73": "WETH",
                   "0x5fc5360d0400a0fd4f2af552add042d716f1d168": "USDG"},
        "native": "ETH", "gas_usd": 0.05, "mev": 0.002, "sniper": 0.005,
        "protect": "a 1% slippage limit (Robinhood Chain orders transactions first-come, first-served, so classic "
                   "sandwiches are harder, but not impossible)",
        "explorer": None,   # no verified explorer link yet: the DexScreener page is used
    },
}
STABLES = {"USDC", "USDT", "DAI", "USDG", "USDE", "FDUSD", "USD1", "PYUSD", "TUSD", "BUSD", "USDS", "USDD", "FRAX",
           "LUSD", "GHO", "CRVUSD", "USD0", "EURC", "USDB", "RLUSD", "SUSDE"}
# symbols a scammer would impersonate: only the canonical (quote) addresses may use them
PROTECTED = {"USDC", "USDT", "DAI", "USDG", "ETH", "WETH", "SOL", "WSOL", "BNB", "WBNB"}
# not trading ideas (stable or wrapped majors): skipped quietly
NOT_IDEAS = STABLES | {"BTC", "WBTC", "CBBTC", "TBTC", "STETH", "WSTETH", "JITOSOL", "MSOL", "BNSOL", "SLISBNB"}
NATIVE_WRAPPED = {"SOL", "WSOL", "WETH", "ETH", "WBNB", "BNB"}


def norm(chain: str, addr: str | None) -> str:
    if not addr:
        return ""
    return addr if chain == "solana" else addr.lower()


def quote_symbol(chain: str, addr: str | None) -> str | None:
    return CHAINS[chain]["quotes"].get(norm(chain, addr))
