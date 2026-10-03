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

tab_top, tab_mine, tab_track, tab_eq, tab_sig, tab_trust, tab_trades, tab_bt, tab_rt, tab_help = st.tabs(
    ["⭐ Live ideas", "🧭 My trades", "📈 Track record", "💼 Pretend account", "BTC/ETH engine",
     "🛡 Fake-signal check", "Trades", "Past-data test", "Manipulation test", "❓ Help & glossary"])

# ---------------------------------------------------------------------------------- live scanner
import base64  # noqa: E402

from scanner import live as LV  # noqa: E402
from scanner.model import ScannerModel  # noqa: E402
from scanner.styles import DEFAULT_STYLE, STYLES, human_duration  # noqa: E402

REFRESH = {"Off (only when I click Refresh)": 0, "Every 1 minute": 60, "Every 2 minutes": 120,
           "Every 5 minutes": 300, "Every 15 minutes": 900, "Every 30 minutes": 1800, "Every hour": 3600}


def _px(x: float) -> str:
    return f"${x:,.2f}" if x >= 1 else f"${x:.4g}"


@st.cache_resource(show_spinner="Preparing the model for this speed (first time only, about a minute)…")
def _model(style: str) -> ScannerModel:
    if not ScannerModel.exists(LV.models_dir(), style):
        from scanner.run import train
        train([style])
    return ScannerModel.load(LV.models_dir(), style)


@st.cache_data(ttl=600, show_spinner=False)
def _universe():
    from scanner.data import select_universe
    return select_universe()


@st.cache_data(ttl=3600, max_entries=12, show_spinner="Fetching fresh prices and re-ranking ~60 coins…")
def _scan(style: str, bucket: int, extra: tuple = ()) -> tuple[dict, dict]:
    res = LV.scan(style, _model(style), _universe(), 5, list(extra))
    return res.payload, res.scores


@st.cache_data(ttl=15, show_spinner=False)
def _prices(symbols: tuple) -> dict:
    return LV.current_prices(list(symbols))


def _load_trades() -> list[dict]:
    if "trades" not in st.session_state:
        raw = st.query_params.get("t")
        try:
            st.session_state.trades = json.loads(base64.urlsafe_b64decode(raw.encode()).decode()) if raw else []
        except Exception:  # noqa: BLE001
            st.session_state.trades = []
    return st.session_state.trades


def _save_trades(trades: list[dict]) -> None:
    st.session_state.trades = trades
    if trades:
        st.query_params["t"] = base64.urlsafe_b64encode(json.dumps(trades, separators=(",", ":")).encode()).decode()
    elif "t" in st.query_params:
        del st.query_params["t"]


with st.sidebar:
    st.header("⚙️ Settings")
    style_key = st.radio("Trading speed (how long you hold a trade)", list(STYLES),
                         index=list(STYLES).index(DEFAULT_STYLE), format_func=lambda k: STYLES[k].label)
    refresh_label = st.selectbox("Refresh the ideas", list(REFRESH), index=3)
    refresh_s = REFRESH[refresh_label]
    if st.button("🔄 Refresh now", use_container_width=True):
        _scan.clear()
        _prices.clear()
    st.caption("Faster refresh = newer prices. The ideas themselves are re-ranked when a new candle closes "
               f"(every {STYLES[style_key].bar_minutes} minutes for this speed).")

def _bucket() -> int:
    """Cache key that changes once per refresh period, so every panel shares one scan."""
    return int(pd.Timestamp.now(tz="UTC").timestamp() // max(refresh_s or 600, 60))


if st.session_state.get("flash"):
    st.toast(st.session_state.pop("flash"), icon="✅")

with tab_top:
    @st.fragment(run_every=refresh_s or None)
    def live_ideas():
        style = STYLES[style_key]
        try:
            payload, _ = _scan(style_key, _bucket())
        except Exception as e:  # noqa: BLE001
            st.error(f"Couldn't reach the market data right now ({e}). It will retry on the next refresh.")
            return
        mood = payload["market_mood"]
        st.subheader(f"Top 5 BUY ideas · {style.label}")
        st.caption(f"Updated {payload['generated_at'][11:19]} UTC · {payload['coins_scanned']} coins checked · "
                   f"refresh: {refresh_label.lower()} · market mood: **{mood['label']}**. "
                   "Score: 10 = the computer likes it a lot, 5 = break-even after fees, below 5 = avoid.")
        if mood["label"] == "Unfavourable":
            st.warning("Most coins look weak at this speed right now. Doing nothing is a good choice.")
        for d in payload["ideas"]:
            with st.container(border=True):
                a, b, c, e = st.columns([2, 1, 1, 1])
                a.markdown(f"### {d['rank']}. {d['coin']}")
                a.markdown(f"**{d['grade']}** · risk **{d['risk_level']}** · hold up to **{d['hold_text']}**")
                b.metric("Score", f"{d['score']}/10")
                c.metric("Chance it beats the market", f"{d['chance_beats_market']:.0%}",
                         help="Chance this coin does better than the average coin over the same period.")
                e.metric("To risk $10, buy", f"${d['size_for_10usd_risk']:,.0f}")
                x, y, z, w = st.columns(4)
                x.metric("Buy near", _px(d["price_now"]))
                y.metric("Take profit at", _px(d["take_profit"]), f"+{d['take_profit_pct']:.1%}")
                z.metric("Safety exit at", _px(d["safety_exit"]), f"{d['safety_exit_pct']:.1%}")
                w.metric("Sell by (UTC)", pd.Timestamp(d["exit_by"]).strftime("%H:%M"),
                         help="If neither exit is hit by then, sell at whatever the price is.")
                st.markdown("**Why:** " + " ".join(d["why"]))
                for warn in d["warnings"]:
                    st.warning(warn)
                if st.button(f"✅ I bought {d['coin']}: watch this trade for me", key=f"take_{style_key}_{d['coin']}"):
                    trades = _load_trades()
                    trades.append(LV.make_trade(d))
                    _save_trades(trades)
                    st.session_state.flash = (f"Watching your {d['coin']} trade. Open the 🧭 My trades tab: it tells "
                                              "you when to sell. Bookmark the page to keep your trades.")
                    st.rerun()  # whole page, so the My trades tab updates immediately

    live_ideas()

with tab_mine:
    st.markdown("Tell the app which trades you took. It watches the live price and tells you **what to do now**: "
                "hold, take profit, use the safety exit, or sell because time is up. "
                "**Bookmark this page after adding a trade.** Your trades are saved in the page address.")

    @st.fragment(run_every=min(refresh_s, 60) if refresh_s else None)
    def my_trades():
        trades = _load_trades()
        if not trades:
            st.info("No trades yet. Click “I bought …” on an idea, or add one below.")
        else:
            prices = _prices(tuple(sorted({t["symbol"] for t in trades})))
            for i, t in enumerate(list(trades)):
                px = prices.get(t["symbol"])
                if px is None:
                    st.error(f"No price for {t['symbol']}")
                    continue
                score = None
                try:
                    _, scores = _scan(t["style"], _bucket())
                    score = scores.get(t["symbol"])
                except Exception:  # noqa: BLE001
                    pass
                adv = LV.advise(t, px, score=score)
                box = {"success": st.success, "error": st.error, "warning": st.warning, "info": st.info}[adv["level"]]
                with st.container(border=True):
                    box(f"**{t['symbol'][:-4]} → {adv['action']}**. {adv['why']}")
                    a, b, c, c2, d = st.columns(5)
                    a.metric("Bought at", _px(t["entry"]))
                    b.metric("Price now", _px(px), f"{adv['pnl_pct']:+.2%} after fees")
                    c.metric("Take profit at", _px(t["take_profit"]), f"{adv['to_take_profit_pct']:+.1%} away",
                             delta_color="off")
                    c2.metric("Safety exit at", _px(t["safety_exit"]), f"{adv['to_safety_exit_pct']:+.1%} away",
                              delta_color="off")
                    d.metric("Time left", human_duration(adv["minutes_left"]) if adv["minutes_left"] > 0 else "none",
                             help=f"Sell by {t['exit_by'][:16]} UTC at the latest.")
                    st.progress(adv["progress"], text="safety exit ◀──── price ────▶ take profit")
                    if st.button("I've sold it: remove", key=f"rm_{i}_{t['symbol']}_{t['opened']}"):
                        trades.pop(i)
                        _save_trades(trades)
                        st.rerun()
        st.caption(f"Prices checked {pd.Timestamp.now(tz='UTC'):%H:%M:%S} UTC.")

    my_trades()

    with st.expander("➕ Add a trade I made myself"):
        uni = _universe()
        with st.form("manual"):
            coin = st.selectbox("Coin", uni["symbol"].tolist(), format_func=lambda s: s[:-4])
            sk = st.selectbox("How long do you plan to hold?", list(STYLES), index=list(STYLES).index(style_key),
                              format_func=lambda k: STYLES[k].label)
            price_now = float(uni.set_index("symbol").loc[coin, "lastPrice"]) if coin else 0.0
            entry = st.number_input("Price I paid (in $)", value=price_now, format="%.8g", min_value=0.0)
            ago = st.number_input("How many minutes ago did you buy?", value=0, min_value=0, step=5)
            risk = st.slider("Safety exit: sell if it falls this % below my price", 0.5, 15.0, 3.0, 0.5,
                             help="The take-profit is set at twice this distance above your price (2 to 1).")
            if st.form_submit_button("Watch this trade"):
                opened = pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=int(ago))
                trades = _load_trades()
                trades.append(LV.manual_trade(coin, sk, float(entry), opened, float(risk)))
                _save_trades(trades)
                st.rerun()

with tab_track:
    board = _json(STATE / "suggestions" / "scoreboard.json")
    st.markdown("Every idea is checked afterwards against what the price really did: bought at the next candle's "
                "price, sold at take-profit, at the safety exit, or at the time limit, minus fees. "
                "**This is the honest test of whether the ideas are any good.**")
    if board.get("closed"):
        rows = []
        for k, v in board.get("by_style", {}).items():
            if v.get("closed"):
                rows.append({"Speed": STYLES[k].label, "Ideas checked": v["closed"],
                             "Ended in profit": f"{v['win_rate']:.0%}",
                             "Average per idea": f"{v['avg_return_per_idea']:+.2%}",
                             "Random pick average": f"{v['random_pick_avg_return']:+.2%}",
                             "$100 in each idea": f"${v['if_100usd_each_total_pnl']:+,.0f}"})
        st.table(pd.DataFrame(rows))
        st.caption("If an idea list doesn't beat 'random pick' over a few weeks, its ranking isn't adding value.")
        hp = STATE / "suggestions" / "history.csv"
        if hp.exists():
            st.dataframe(pd.read_csv(hp).iloc[::-1], use_container_width=True)
    else:
        st.info("Ideas are settled once their time limit has passed. Check back later.")

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
