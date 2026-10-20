"""Engine A's data sources: all free, no key, no manual input (owner's requirement, D121). Each loader returns plain
values with their dates; nothing missing is ever filled with a default.

  FRED (Federal Reserve): WALCL, RRPONTSYD, WTREGEN as public CSV downloads
  DefiLlama: total stablecoin supply; fees and holders revenue (buybacks, burns, staker share); token unlock schedules
             (the open emissions data DefiLlama publishes for its own site: attributed, fetched once a day)
  Binance public market data: daily candles for every USDT coin, delisted ones included (history for the research)
  Coinbase public market data: which coins trade against USD/USDC, order books, daily candles (the live universe)
  OKX public data: perpetual futures funding and open interest
  CoinGecko: supply, market cap, FDV; each coin's GitHub repository
  GitHub API: commit activity (the scheduled job's own token)
"""
from __future__ import annotations

import csv
import io
import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger("hayes.data")
CACHE = Path(".cache/hayes")
S = requests.Session()
S.headers["User-Agent"] = "mempool-omega research (paper trading)"


def get(url: str, params: dict | None = None, tries: int = 4, timeout: int = 60, pace: float = 0.0):
    err = None
    for i in range(tries):
        if pace:
            time.sleep(pace)
        try:
            r = S.get(url, params=params, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                err = f"HTTP {r.status_code}"
                time.sleep(min(30, 2 ** (i + 1)))
                continue
            if r.status_code != 200:
                return None
            return r
        except requests.RequestException as e:
            err = str(e)
            time.sleep(min(30, 2 ** (i + 1)))
    log.warning("%s failed: %s", url, err)
    return None


def fred(series: str, tries: int = 4) -> list[tuple[str, float]]:
    """FRED's public CSV. FRED's firewall stalls Python's HTTP client but serves the same file to curl (on every
    GitHub runner), so the download runs through curl itself."""
    text = ""
    for i in range(tries):
        try:
            p = subprocess.run(["curl", "-sSf", "--max-time", "90", f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"],
                               capture_output=True, text=True, timeout=120)
            if p.returncode == 0 and p.stdout.startswith("observation_date"):
                text = p.stdout
                break
            log.warning("FRED %s: curl exit %s %s", series, p.returncode, p.stderr.strip()[:200])
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("FRED %s: %s", series, e)
        time.sleep(min(30, 2 ** (i + 1)))
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) == 2 and row[0][:2] in ("19", "20"):
            try:
                out.append((row[0], float(row[1])))
            except ValueError:
                continue                                   # "." marks a missing observation: skipped, never zero
    return out


def stablecoins() -> list[tuple[str, float]]:
    r = get("https://stablecoins.llama.fi/stablecoincharts/all")
    out = []
    for x in (r.json() if r else []):
        try:
            out.append((datetime.fromtimestamp(int(x["date"]), tz=timezone.utc).date().isoformat(), float(x["totalCirculatingUSD"]["peggedUSD"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def binance_symbols() -> list[dict]:
    r = get("https://data-api.binance.vision/api/v3/exchangeInfo")
    return [s for s in (r.json().get("symbols", []) if r else []) if s.get("quoteAsset") == "USDT"]


def binance_daily(symbol: str, have: list | None = None, now_ms: int | None = None) -> list[list]:
    """Full daily history, topped up from what's cached: [open_ms, o, h, l, c, base_vol, close_ms, quote_vol]."""
    ks = list(have or [])
    start = ks[-1][0] + 86_400_000 if ks else 0
    while True:
        r = get("https://data-api.binance.vision/api/v3/klines", {"symbol": symbol, "interval": "1d", "startTime": start, "limit": 1000}, pace=0.05)
        part = r.json() if r else []
        if not part:
            break
        ks += [[x[0], x[1], x[2], x[3], x[4], x[5], x[6], x[7]] for x in part if not ks or x[0] > ks[-1][0]]
        if len(part) < 1000:
            break
        start = part[-1][0] + 86_400_000
    now_ms = now_ms or int(time.time() * 1000)
    return [k for k in ks if k[6] < now_ms]                 # finished days only


def to_candles(ks: list[list]) -> list[dict]:
    return [{"d": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).date().isoformat(), "o": float(k[1]), "h": float(k[2]),
             "l": float(k[3]), "c": float(k[4]), "v": float(k[7])} for k in ks]


def jload(p: Path, default):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def jsave(p: Path, d):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, separators=(",", ":"), default=str))
