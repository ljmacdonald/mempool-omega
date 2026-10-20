"""Engine B's data-coverage check (D122): runs before any performance result. Survivorship is the central risk for
deep-distress research: free sources keep dead coins poorly, which makes recoveries look common.

Known coins: every base asset any free source still lists, live or delisted: Binance USDT pairs (trading and
delisted, "BREAK") and Coinbase USD/USDC products (online and delisted). Covered: coins with complete daily candles
from either exchange. Known band entries = entries found in covered coins + an estimate for known coins without
candles (their count x the entry rate among covered coins). Coins removed from every free list can't be counted (CoinGecko's
dead-coin list is paid only), so the true coverage is lower than measured: the report says so. Below min_ratio every
figure is labelled UNRELIABLE: survivorship and Engine B cannot pass validation.
"""
from __future__ import annotations

from deep.profile import load


def report(known: dict[str, set[str]], covered: set[str], entries_by_coin: dict[str, int], profile: dict | None = None) -> dict:
    """known: coin -> the sources that list it; covered: coins with full candles; entries_by_coin: band entries found."""
    P = (profile or load())["coverage"]
    uncovered = sorted(set(known) - covered)
    found = sum(entries_by_coin.get(c, 0) for c in covered)
    rate = found / len(covered) if covered else 0.0
    est_missing = rate * len(uncovered)
    ratio = found / (found + est_missing) if (found + est_missing) > 0 else 0.0
    ok = ratio >= P["min_ratio"] and found > 0
    by_source: dict[str, int] = {}
    for srcs in known.values():
        for s in srcs:
            by_source[s] = by_source.get(s, 0) + 1
    return {"since": str(P["since"]), "known_coins": len(known), "covered_coins": len(covered), "uncovered_coins": len(uncovered),
            "entries_found": found, "entries_estimated_missing": round(est_missing, 1), "ratio": ratio, "min_ratio": P["min_ratio"],
            "reliable": ok, "label": None if ok else "UNRELIABLE: survivorship", "sources": by_source, "uncovered_sample": uncovered[:40],
            "known_gaps": ["Coins removed from every free exchange list since 2019 (CoinGecko's dead-coin list needs the paid plan): not counted, so true coverage is lower than measured.",
                           "Coins that only traded on other exchanges or on DEXes are not in the known set.",
                           "Uncovered coins are assumed to have crashed as often as covered ones; dead coins likely crashed more, so the estimate is optimistic."]}
