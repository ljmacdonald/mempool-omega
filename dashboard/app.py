"""Mempool Omega - Streamlit dashboard (reads the committed state/ folder; no secrets needed).

Run locally:      streamlit run dashboard/app.py
Streamlit Cloud:  point the app at this file (see RUNBOOK.md).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
STATE = Path(os.environ.get("OMEGA_STATE_DIR", ROOT / "state"))

st.set_page_config(page_title="Mempool Omega", page_icon="Ω", layout="wide")


def _json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def _ledger(kind: str) -> pd.DataFrame:
    files = sorted((STATE / "ledger").glob(f"{kind}_*.parquet"))
    if not files:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def _line(df: pd.DataFrame, x: str, y: str, color: str = "#2563eb") -> None:
    """Line chart whose y-axis fits the data (st.line_chart starts at 0, which flattens equity curves)."""
    import altair as alt

    ch = (alt.Chart(df).mark_line(color=color, strokeWidth=2)
          .encode(x=alt.X(f"{x}:T", title=None), y=alt.Y(f"{y}:Q", scale=alt.Scale(zero=False), title=y),
                  tooltip=[x, y]).properties(height=320))
    st.altair_chart(ch, use_container_width=True)


FMT = {"total_return": "{:.2%}", "max_drawdown": "{:.2%}", "hit_rate": "{:.1%}", "sharpe": "{:.2f}",
       "n_trades": "{:d}"}

status = _json(STATE / "status.json")
st.title("Ω Mempool Omega")
st.caption("Adversarial-aware intraday crypto research · **PAPER TRADING ONLY** · not financial advice")

if not status:
    st.warning("No paper-trading run yet. Run `make paper` or wait for the hourly GitHub Action.")
else:
    ks = status.get("kill_switch", {})
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Paper equity", f"${status.get('equity', 0):,.0f}")
    c2.metric("Open positions", len(status.get("positions", {})))
    c3.metric("Systemic trust", f"{(status.get('systemic_trust') or 0):.2f}")
    c4.metric("Kill switch", "ACTIVE" if ks.get("active") else "armed")
    c5.metric("Data mode", status.get("mode", "?"))
    st.caption(f"Last run: {status.get('run_at')}")
    if ks.get("active"):
        st.error(f"Kill switch active since {ks.get('since')}: {ks.get('reason')}")

tab_eq, tab_sig, tab_trust, tab_trades, tab_bt, tab_rt = st.tabs(
    ["Equity", "Signals", "Trust (ASI)", "Trades", "Backtest", "Red team"])

with tab_eq:
    eq = _ledger("equity")
    if len(eq) >= 2:
        eq["ts"] = pd.to_datetime(eq["ts"], utc=True)
        _line(eq, "ts", "equity")
        st.dataframe(eq.tail(20).iloc[::-1], use_container_width=True)
    elif len(eq):
        st.info("The equity chart appears after the second hourly paper run.")
        st.dataframe(eq, use_container_width=True)
    else:
        st.info("No equity history yet.")
    if status.get("positions"):
        st.subheader("Open positions")
        st.dataframe(pd.DataFrame(status["positions"]).T, use_container_width=True)

with tab_sig:
    for sym, info in status.get("symbols", {}).items():
        st.subheader(sym)
        a, b, c, d = st.columns(4)
        a.metric("Close", f"{info.get('close', 0):,.2f}")
        b.metric("P(up)", f"{info.get('p_up', 0):.3f}")
        c.metric("Meta P(take)", f"{info.get('meta_p', 0):.3f}")
        d.metric("Signal trust", f"{info.get('signal_trust', 0):.2f}")
        st.json({"data_quality": info.get("data_quality"), "sources": info.get("sources")}, expanded=False)
    sig = _ledger("signals")
    if len(sig):
        st.subheader("Recent decisions")
        st.dataframe(sig.tail(50).iloc[::-1], use_container_width=True)

with tab_trust:
    st.markdown("Trust = w1·cost_to_fake + w2·cross_source + w3·persistence + w4·(1−anomaly) + w5·graph + w6·time. "
                "Trade only when **edge > cost + manipulation premium(trust)**.")
    for sym, info in status.get("symbols", {}).items():
        st.subheader(sym)
        left, right = st.columns(2)
        left.write("Least trusted features")
        left.bar_chart(pd.Series(info.get("least_trusted_features", {})))
        right.write("Most trusted features")
        right.bar_chart(pd.Series(info.get("most_trusted_features", {})))
    ev = _ledger("events")
    if len(ev):
        st.subheader("Events (entries, exits, trust drops, kill switch)")
        st.dataframe(ev.tail(50).iloc[::-1], use_container_width=True)

with tab_trades:
    tr = _ledger("trades")
    if len(tr):
        st.metric("Closed trades", len(tr))
        st.bar_chart(tr["exit_reason"].value_counts())
        st.dataframe(tr.iloc[::-1], use_container_width=True)
    else:
        st.info("No closed paper trades yet.")

with tab_bt:
    bt = _json(STATE / "reports" / "backtest_latest.json")
    if bt:
        cols = st.columns(5)
        for col, k in zip(cols, ("total_return", "sharpe", "max_drawdown", "n_trades", "hit_rate")):
            v = bt.get(k)
            col.metric(k.replace("_", " "), FMT[k].format(v) if v is not None else "–")
        st.caption(f"data_mode={bt.get('data_mode')} · {bt.get('start')} → {bt.get('end')}")
        p = STATE / "reports" / "backtest_latest_equity.csv"
        if p.exists():
            curve = pd.read_csv(p)
            curve.columns = ["ts", "equity"]
            _line(curve, "ts", "equity")
        st.caption("Walk-forward, out-of-sample, after fees, slippage, latency and funding. "
                   "A handful of trades is not statistically meaningful.")
        st.json(bt, expanded=False)
    else:
        st.info("No backtest report yet (`make backtest`).")

with tab_rt:
    rt = _json(STATE / "reports" / "redteam_latest.json")
    if rt:
        rows = []
        for name, r in rt.get("attacks", {}).items():
            rows.append({"attack": name, "naive_bait_decisions": r["naive"]["bait_decisions"],
                         "asi_bait_decisions": r["asi"]["bait_decisions"],
                         "naive_bait_pnl": r["naive"]["bait_pnl"], "asi_bait_pnl": r["asi"]["bait_pnl"],
                         "naive_return": r["naive"]["total_return"], "asi_return": r["asi"]["total_return"]})
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
        st.caption("Red-team runs use SYNTHETIC data with injected attacks. They test the defences, not real-market alpha.")
    else:
        st.info("No red-team report yet (`make redteam`).")

st.divider()
st.caption("Mempool Omega is research software. No profit is promised or implied. See LEGAL.md.")
