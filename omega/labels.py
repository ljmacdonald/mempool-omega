"""Triple-barrier labels (Lopez de Prado) consistent with the live exit rules.

For each bar t (entry at close_t):
  upper = close_t * exp(+pt * sigma_t * sqrt(H)),  lower = close_t * exp(-sl * sigma_t * sqrt(H))
scan bars t+1..t+H using high/low; first touch wins; otherwise time-out at H.
If a bar touches both, the stop is assumed first (conservative).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def triple_barrier(raw: pd.DataFrame, sigma: pd.Series, horizon: int, pt: float, sl: float,
                   side: np.ndarray | None = None) -> pd.DataFrame:
    """Return DataFrame[ret, outcome(+1 pt / -1 sl / 0 time), t_exit(bars), label_long] per bar.

    ``ret`` is the log return of a LONG (or of ``side`` if given) from close_t to the exit price.
    """
    c = raw["close"].to_numpy()
    h = raw["high"].to_numpy()
    lo = raw["low"].to_numpy()
    s = sigma.to_numpy() * np.sqrt(horizon)
    n = len(c)
    side = np.ones(n) if side is None else np.asarray(side, dtype=float)
    ret = np.full(n, np.nan)
    outcome = np.zeros(n)
    t_exit = np.full(n, np.nan)
    for t in range(n - 1):
        if not np.isfinite(s[t]) or s[t] <= 0 or side[t] == 0:
            continue
        end = min(t + horizon, n - 1)
        if side[t] > 0:
            up, dn = c[t] * np.exp(pt * s[t]), c[t] * np.exp(-sl * s[t])
        else:
            up, dn = c[t] * np.exp(sl * s[t]), c[t] * np.exp(-pt * s[t])
        done = False
        for k in range(t + 1, end + 1):
            hit_up, hit_dn = h[k] >= up, lo[k] <= dn
            if side[t] > 0 and (hit_dn or hit_up):
                px, o = (dn, -1) if hit_dn else (up, 1)
            elif side[t] < 0 and (hit_up or hit_dn):
                px, o = (up, -1) if hit_up else (dn, 1)
            else:
                continue
            ret[t] = side[t] * np.log(px / c[t])
            outcome[t], t_exit[t], done = o, k - t, True
            break
        if not done and end > t:
            ret[t] = side[t] * np.log(c[end] / c[t])
            t_exit[t] = end - t
            if end - t < horizon:  # ran out of data: unknown
                ret[t] = np.nan
    out = pd.DataFrame({"ret": ret, "outcome": outcome, "t_exit": t_exit}, index=raw.index)
    out["label_long"] = (out["ret"] > 0).astype(float).where(out["ret"].notna())
    return out
