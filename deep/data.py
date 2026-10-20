"""Engine B's extra data: Coinbase public market data (no key) for coins Binance never listed, delisted ones included.
Everything else (FRED, stablecoins, Binance candles, DefiLlama) comes from hayes.data, so both engines read the same
numbers (D122).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hayes.data import get

CB = "https://api.exchange.coinbase.com"


def coinbase_products() -> list[dict]:
    """USD and USDC products, online and delisted."""
    r = get(f"{CB}/products")
    return [p for p in (r.json() if r else []) if p.get("quote_currency") in ("USD", "USDC")]


def coinbase_daily(product: str, since: str = "2017-01-01", now: datetime | None = None) -> list[dict]:
    """Finished daily candles {d, o, h, l, c, v} (v in US dollars: base volume x close), oldest first. Coinbase serves
    300 days per call, newest first; delisted products keep their history."""
    now = now or datetime.now(timezone.utc)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
    rows: dict[int, list] = {}
    while end > start:
        a = max(start, end - timedelta(days=300))
        r = get(f"{CB}/products/{product}/candles", {"granularity": 86400, "start": a.isoformat(), "end": end.isoformat()}, pace=0.12)
        part = r.json() if r else []
        if isinstance(part, list):
            for x in part:
                rows[int(x[0])] = x
        end = a
        if r is not None and not part and rows:
            break                                           # before the product's first day
    today = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    return [{"d": datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat(), "o": float(x[3]), "h": float(x[2]),
             "l": float(x[1]), "c": float(x[4]), "v": float(x[5]) * float(x[4])} for t, x in sorted(rows.items()) if t < today]
