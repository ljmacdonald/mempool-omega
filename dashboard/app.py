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
st.caption("A computer that studies the crypto market every hour and practises trading with **pretend money**. "
           "Nothing here is financial advice, and no profit is promised.")

if not status:
    st.warning("No paper-trading run yet. Run `make paper` or wait for the hourly GitHub Action.")
else:
    ks = status.get("kill_switch", {})
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Pretend balance", f"${status.get('equity', 0):,.0f}", help="Started at $100,000 of pretend money.")
    c2.metric("Open BTC/ETH trades", len(status.get("positions", {})))
    c3.metric("Data trust (0-1)", f"{(status.get('systemic_trust') or 0):.2f}",
              help="How much the computer believes today's market signals are real rather than faked. "
                   "Above 0.6 is normal.")
    c4.metric("Emergency stop", "TRIGGERED" if ks.get("active") else "ready",
              help="Closes everything if losses or data problems get too big.")
    c5.metric("Data", "live market" if status.get("mode") != "synthetic" else "practice data")
    st.caption(f"Last run: {status.get('run_at')}")
    if ks.get("active"):
        st.error(f"Kill switch active since {ks.get('since')}: {ks.get('reason')}")

tab_top, tab_track, tab_eq, tab_sig, tab_trust, tab_trades, tab_bt, tab_rt, tab_help = st.tabs(
    ["⭐ Top 5 ideas", "📈 Track record", "💼 Pretend account", "BTC/ETH engine", "🛡 Fake-signal check",
     "Trades", "Past-data test", "Manipulation test", "❓ Help & glossary"])

suggest = _json(STATE / "suggestions" / "latest.json")


def _px(x: float) -> str:
    return f"${x:,.2f}" if x >= 1 else f"${x:.6g}"


with tab_top:
    if not suggest:
        st.info("The first list appears after the next hourly run.")
    else:
        mood = suggest.get("market_mood", {})
        st.subheader(f"This hour's 5 best-ranked BUY ideas · market mood: {mood.get('label', '?')}")
        st.caption(f"Made {suggest['generated_at'][:16]} UTC from {suggest['coins_scanned']} coins. "
                   "Score: 10 = the computer likes it a lot, 5 = break-even, below 5 = avoid. "
                   "Every idea comes with a planned take-profit, a safety exit, and a 24-hour time limit.")
        if mood.get("label") == "Unfavourable":
            st.warning("The computer thinks most coins look weak right now. Sitting out is a good choice.")
        for d in suggest["ideas"]:
            with st.container(border=True):
                a, b, c, e = st.columns([2, 1, 1, 1])
                a.markdown(f"### {d['rank']}. {d['coin']}")
                a.markdown(f"**{d['grade']}** · risk **{d['risk_level']}**")
                b.metric("Score", f"{d['score']}/10")
                c.metric("Chance of profit", f"{d['chance_of_profit']:.0%}")
                e.metric("To risk $10, buy", f"${d['size_for_10usd_risk']:,.0f}")
                x, y, z = st.columns(3)
                x.metric("Buy near", _px(d["price_now"]))
                y.metric("Take profit", _px(d["take_profit"]), f"+{d['take_profit_pct']:.1%}")
                z.metric("Safety exit", _px(d["safety_exit"]), f"{d['safety_exit_pct']:.1%}")
                st.markdown("**Why:** " + " ".join(d["why"]))
                for w in d["warnings"]:
                    st.warning(w)
                if d.get("week_up20_pct") is not None:
                    st.caption(f"Last 90 days: rose 20%+ within a week {d['week_up20_pct']:.0%} of the time, "
                               f"fell 20%+ {d['week_down20_pct']:.0%} of the time.")

with tab_track:
    board = _json(STATE / "suggestions" / "scoreboard.json")
    st.markdown("Every idea is checked afterwards against what the price really did: bought at the next "
                "hour's price, sold at take-profit, at the safety exit, or after 24 hours, minus fees. "
                "**This is the honest test of whether the ideas are any good.**")
    if board.get("closed"):
        a, b, c, d = st.columns(4)
        a.metric("Ideas checked", board["closed"])
        b.metric("Ended in profit", f"{board['win_rate']:.0%}")
        c.metric("Average per idea", f"{board['avg_return_per_idea']:+.2%}",
                 f"random pick: {board['random_pick_avg_return']:+.2%}", delta_color="off")
        d.metric("$100 in each idea", f"${board['if_100usd_each_total_pnl']:+,.0f}")
        hp = STATE / "suggestions" / "history.csv"
        if hp.exists():
            h = pd.read_csv(hp)
            st.dataframe(h.iloc[::-1], use_container_width=True)
    else:
        st.info("Ideas are settled 24 hours after they are made. Check back tomorrow.")

with tab_help:
    st.markdown((ROOT / "docs" / "BEGINNERS_GUIDE.md").read_text() if (ROOT / "docs" / "BEGINNERS_GUIDE.md").exists()
                else "See docs/BEGINNERS_GUIDE.md")

with tab_eq:
    st.caption("The BTC/ETH engine practises with $100,000 of pretend money. This chart shows that balance over time.")
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
    st.caption("The main engine only trades Bitcoin and Ether, and only when its expected gain beats fees plus a "
               "safety margin. 'P(up)' = its estimated chance that the price goes up next.")
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
    st.markdown("Some market signals are cheap to fake (for example, big buy orders that vanish before anyone "
                "fills them). Each signal gets a **trust score from 0 to 1**: low means 'this might be bait'. "
                "The computer ignores low-trust signals and demands a bigger expected gain when trust is low.")
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
    st.caption("Every pretend BTC/ETH trade, and which of the six exit rules closed it (see Help).")
    tr = _ledger("trades")
    if len(tr):
        st.metric("Closed trades", len(tr))
        st.bar_chart(tr["exit_reason"].value_counts())
        st.dataframe(tr.iloc[::-1], use_container_width=True)
    else:
        st.info("No closed paper trades yet.")

with tab_bt:
    st.caption("What would have happened over the last ~3 weeks if the engine had traded then, using only "
               "information it would have had at the time, after fees.")
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
    st.caption("We fake common market tricks (spoofing, fake volume, etc.) on practice data and count how often "
               "the computer falls for them, with and without its fake-signal check.")
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
