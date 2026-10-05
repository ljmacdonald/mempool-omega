/* Mempool Omega US stocks page. Rankings: the project's 15-minute snapshot (GitHub Actions reads Nasdaq and
   Yahoo, which web pages can't read directly). Live prices: optional Finnhub key, kept in this browser only. */
(function () {
  "use strict";
  const E = window.OmegaEngine;
  const DATA = window.OMEGA_STOCK_DATA || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/data/stocks/";
  const REPO = window.OMEGA_REPO || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/main/state/";
  const GRADES = [[6.5, "Strong"], [5.6, "Moderate"], [5.0, "Weak"], [-1, "Avoid - watch only"]];
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
  };
  const state = {
    list: store.get("omega.stk.list", "large"), style: store.get("omega.stk.style", "stk_today"),
    refresh: store.get("omega.stk.refresh", 300), amount: store.get("omega.stk.amount", 100), fx: store.get("omega.stk.fx", 0.01),
    key: store.get("omega.stk.finnhub", ""), snap: null, snapAt: 0, live: {}, ideas: [], timer: null, nextAt: 0, busy: false,
    trades: store.get("omega.stk.trades", []), anchors: store.get("omega.stk.sugg", {}), charts: {}, lastAction: {},
  };

  // ------------------------------------------------------------------ formatting & time
  const price = (x) => (!Number.isFinite(x) ? "–" : x >= 1000 ? `$${x.toLocaleString("en-US", { maximumFractionDigits: 2 })}` : `$${x.toFixed(2)}`);
  const pct = (x, d = 1) => (Number.isFinite(x) ? `${x >= 0 ? "+" : ""}${(x * 100).toFixed(d)}%` : "–");
  const usd = (x) => (Number.isFinite(x) ? `${x >= 0 ? "+" : "−"}$${Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "–");
  const big = (x) => (x >= 1e12 ? `$${(x / 1e12).toFixed(1)}T` : x >= 1e9 ? `$${(x / 1e9).toFixed(1)}B` : x >= 1e6 ? `$${(x / 1e6).toFixed(0)}M` : `$${Math.round(x / 1e3)}k`);
  const local = (ms) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const localDay = (ms) => new Date(ms).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
  const nyParts = (ms = Date.now()) => Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(new Date(ms)).map((p) => [p.type, p.value]));
  const nyTime = (ms) => new Date(ms).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" });
  const gradeOf = (s) => GRADES.find(([th]) => s >= th)[1];
  const gradeClass = (g) => (g === "Strong" ? "good" : g === "Moderate" ? "calm" : g === "Weak" ? "warn" : "bad");
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function getJSON(url, timeout = 20000) {
    const ctl = new AbortController(); const tm = setTimeout(() => ctl.abort(), timeout);
    try { const r = await fetch(url, { signal: ctl.signal, cache: "no-store" }); if (!r.ok) throw new Error(`HTTP ${r.status}`); return await r.json(); }
    finally { clearTimeout(tm); }
  }
  async function getText(url) { const r = await fetch(url, { cache: "no-store" }); if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.text(); }

  // ------------------------------------------------------------------ market hours
  function market() {
    const p = nyParts(); const mins = (+p.hour % 24) * 60 + +p.minute; const weekday = !["Sat", "Sun"].includes(p.weekday);
    const m = state.snap?.market || {}; const now = Date.now() / 1000;
    let open = weekday && mins >= 570 && mins < 960;
    if (m.session_start && m.session_end && now - m.session_end < 20 * 3600 && now > m.session_start - 12 * 3600) open = now >= m.session_start && now < m.session_end;
    const closeMs = m.session_end && now < m.session_end ? m.session_end * 1000 : null;
    return { open, mins, weekday, closeMs };
  }
  function paintMarket() {
    const mk = market(); const b = $("market");
    $("clock").textContent = `New York ${nyTime(Date.now())}`;
    if (mk.open) { b.className = "banner good"; b.innerHTML = `<b>US market is open</b> until 4:00 pm New York time${mk.closeMs ? ` (${local(mk.closeMs)} your time)` : ""}.`; }
    else { b.className = "banner warn"; b.innerHTML = `<b>US market is closed.</b> It's open Monday to Friday, 9:30 am to 4:00 pm New York time. Ideas below are from the last update and can't be traded until it opens.`; }
  }

  // ------------------------------------------------------------------ data
  async function loadSnap(force) {
    if (!force && state.snap && Date.now() - state.snapAt < 60000) return state.snap;
    state.snap = await getJSON(DATA + "snapshot.json"); state.snapAt = Date.now();
    return state.snap;
  }
  async function livePrices(symbols) {
    if (!state.key || !symbols.length) return;
    await Promise.all([...new Set(symbols)].slice(0, 25).map(async (s) => {
      try { const q = await getJSON(`https://finnhub.io/api/v1/quote?symbol=${encodeURIComponent(s)}&token=${encodeURIComponent(state.key)}`, 10000);
        if (q && q.c > 0) state.live[s] = { price: q.c, at: Date.now() }; } catch { /* keep the snapshot price */ }
    }));
  }
  // live only (Finnhub key, confirmed in the last 90 s). The 15-minute snapshot price is never shown as "now" while the
  // market is open; when it's closed, the snapshot price is the last close, which is the current price.
  const LIVE_AGE = 90000;
  const livePx = (sym) => E.freshPrice(state.live[sym], LIVE_AGE);
  const nowPx = (row) => livePx(row.symbol) ?? (!market().open ? row.price : null);
  const snapWhen = () => `${nyTime(Date.parse(state.snap.generated_at))} New York`;

  // all-in round trip for this stock and the visitor's currency fee
  const costRt = (row) => row.spread + (state.snap.sec_fee || 0.0000278) + 2 * state.fx;
  function evaluate(row) {
    const c = costRt(row);
    const r = row.p * row.win_r - (1 - row.p) * row.loss_r - c / row.risk_unit - row.flag_penalty;
    const sc = E.scoreFromR(r);
    return { ...row, cost: c, r, scoreNow: sc, gradeNow: gradeOf(sc) };
  }
  function outcome(amount, entry, exit, row) {   // money taken out after spread, SEC fee and currency fees
    const half = row.spread / 2; const shares = amount * (1 - state.fx) / (entry * (1 + half));
    return shares * exit * (1 - half) * (1 - (state.snap?.sec_fee || 0.0000278)) * (1 - state.fx) - amount;
  }

  // ------------------------------------------------------------------ scan
  async function refresh() {
    if (state.busy) return;
    state.busy = true; $("refreshNow").disabled = true; setStatus("Loading the latest stock rankings…");
    try {
      const snap = await loadSnap(true);
      const rows = ((snap.lists || {})[state.list] || {})[state.style] || [];
      await livePrices(rows.slice(0, 8).map((r) => r.symbol).concat(state.trades.map((t) => t.symbol)));
      state.ideas = rows.map(evaluate).sort((a, b) => b.scoreNow - a.scoreNow).slice(0, 5).map((d, i) => ({ ...d, rank: i + 1 }));
      anchor(); render();
      const age = Math.round((Date.now() - Date.parse(snap.generated_at)) / 60000);
      setStatus(`Rankings from ${age} min ago${state.key ? " · live prices from Finnhub" : " · prices refresh every 15 min while the market is open"}`);
    } catch (e) {
      $("mood").className = "banner bad"; $("mood").textContent = `Couldn't load the stock rankings yet (${e.message || e}). They appear after the first run during US market hours.`;
      $("ideas").innerHTML = ""; setStatus("Couldn't load data");
    } finally { state.busy = false; $("refreshNow").disabled = false; schedule(); paintMarket(); }
  }
  function schedule() { clearTimeout(state.timer); if (state.refresh > 0) { state.nextAt = Date.now() + state.refresh * 1000; state.timer = setTimeout(refresh, state.refresh * 1000); } else state.nextAt = 0; }
  function setStatus(t) { $("status").dataset.base = t; paintStatus(); }
  function paintStatus() {
    let extra = ""; if (state.nextAt && !state.busy) { const s = Math.max(0, Math.round((state.nextAt - Date.now()) / 1000)); extra = ` · next in ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; }
    $("status").textContent = ($("status").dataset.base || "") + extra;
  }

  // sell-by: "Today" = 2 hours but before the close; "Few days" = 3 trading days later at the same time
  function exitTime(from) {
    if (state.style === "stk_today") { const mk = market(); const e = from + 2 * 3600000; return mk.closeMs ? Math.min(e, mk.closeMs - 5 * 60000) : e; }
    let t = from, added = 0;
    while (added < 3) { t += 864e5; const wd = nyParts(t).weekday; if (wd !== "Sat" && wd !== "Sun") added++; }
    return t;
  }
  function anchor() {
    const now = Date.now(); const keep = {};
    for (const d of state.ideas) {
      const k = `${state.list}:${state.style}:${d.symbol}`; let a = state.anchors[k];
      if (!a || now > a.exit_by) {
        const lp = livePx(d.symbol); const px = lp ?? d.price; const exitBy = exitTime(now);
        a = { at: lp ? now : Date.parse(state.snap.generated_at), price: px, take_profit: px * (1 + d.take_profit_pct), safety_exit: px * (1 + d.safety_exit_pct), exit_by: exitBy };
      }
      keep[k] = a; d.anchor = a;
    }
    for (const [k, a] of Object.entries(state.anchors)) if (!k.startsWith(`${state.list}:${state.style}:`) && now < a.exit_by + 864e5) keep[k] = a;
    state.anchors = keep; store.set("omega.stk.sugg", keep);
  }

  // ------------------------------------------------------------------ charts
  function drawChart(el, ch, lines, markerMs) {
    if (!el || !ch || !window.LightweightCharts) return null;
    const chart = LightweightCharts.createChart(el, { autoSize: true, layout: { background: { color: cssVar("--surface") }, textColor: cssVar("--muted"), fontFamily: cssVar("--f-num") },
      grid: { vertLines: { color: cssVar("--sunken") }, horzLines: { color: cssVar("--sunken") } }, rightPriceScale: { borderColor: cssVar("--line") },
      timeScale: { borderColor: cssVar("--line"), timeVisible: true, secondsVisible: false }, crosshair: { mode: 0 }, handleScroll: false, handleScale: false });
    const s = chart.addCandlestickSeries({ upColor: cssVar("--good"), downColor: cssVar("--bad"), borderVisible: false, wickUpColor: cssVar("--good"), wickDownColor: cssVar("--bad") });
    s.setData(ch.t.map((t, i) => ({ time: t, open: ch.open[i], high: ch.high[i], low: ch.low[i], close: ch.close[i] })));
    for (const l of lines) s.createPriceLine({ price: l.price, color: cssVar(l.color), lineWidth: 2, lineStyle: l.dashed ? 2 : 0, axisLabelVisible: true, title: l.title });
    chart.timeScale().fitContent();
    return { chart, series: s };
  }
  function dropCharts() { for (const c of Object.values(state.charts)) { try { c.chart.remove(); } catch { /* gone */ } } state.charts = {}; }

  // ------------------------------------------------------------------ render ideas
  function calcRows(el) {
    const d = state.ideas.find((x) => x.symbol === el.dataset.sym); if (!d) return;
    const amt = +el.querySelector(".amt").value || 0; const tgt = +el.querySelector(".tgt").value; const a = d.anchor; const now = nowPx(d);
    const row = (label, x) => { const v = outcome(amt, a.price, x, d); return `<tr><td>${label}</td><td class="num">${price(x)}</td><td class="num ${v >= 0 ? "up" : "down"}">${usd(v)}</td><td class="num muted">${pct(v / amt)}</td></tr>`; };
    const shares = amt * (1 - state.fx) / (a.price * (1 + d.spread / 2));
    let h = `<tr><td colspan="4" class="muted">$${amt.toLocaleString()} buys about <b class="num">${shares.toFixed(shares < 1 ? 4 : 2)}</b> shares at ${price(a.price)} (most brokers allow fractions of a share).</td></tr>`;
    h += row("If it reaches the take profit", a.take_profit) + row("If it hits the safety exit", a.safety_exit);
    h += Number.isFinite(now) && market().open ? row("If you sold right away", now) : `<tr><td>If you sold right away</td><td colspan="3" class="muted">${market().open ? "needs a live price (free Finnhub key above)" : "the market is closed"}</td></tr>`;
    if (tgt > 0) h += row("If you sell at your price", tgt);
    el.querySelector(".calc-out").innerHTML = h;
  }
  function card(d) {
    const a = d.anchor; const now = nowPx(d); const since = Number.isFinite(now) ? now / a.price - 1 : NaN; const st = state.snap.styles[state.style];
    const warns = d.warnings.slice();
    if (state.style === "stk_today") warns.push("Day-trading rule: US margin accounts under $25,000 are limited to 3 same-day round trips in 5 business days.");
    const earn = d.earnings ? `<p class="small muted">Next results ("earnings"): ${esc(d.earnings)}.</p>` : "";
    const id = `chart-${d.symbol}`;
    return `<article class="card">
      <div class="idea-head"><h2><span class="rank">${d.rank}.</span>${esc(d.symbol)} <span class="small muted" style="font-family:var(--f-body);font-weight:500">${esc(d.name)}</span></h2>
        <div class="score"><b>${d.scoreNow.toFixed(1)}</b><span class="muted">/ 10</span><span class="pill ${gradeClass(d.gradeNow)}">${esc(d.gradeNow)}</span></div></div>
      <p class="small muted">${esc(d.sector || "")} · company worth ${big(d.mcap)} · trades ${big(d.dollar_vol)} a day · ${pct(d.chg_pct)} today</p>
      <div class="strip">
        <div class="fact"><div class="k">Suggested</div><div class="v">${localDay(a.at)}</div><div class="d muted">at ${price(a.price)}</div></div>
        <div class="fact"><div class="k">${livePx(d.symbol) ? "Price now" : market().open ? "Price now" : "Last close"}</div><div class="v" data-live="${esc(d.symbol)}">${price(now)}</div><div class="d ${!Number.isFinite(now) ? "muted" : since >= 0 ? "up" : "down"}" data-since="${esc(d.symbol)}">${Number.isFinite(now) ? `${pct(since, 2)} since suggested` : "not live: add a free Finnhub key above, or check your broker"}</div>${livePx(d.symbol) ? '<div class="small muted">Live (Finnhub)</div>' : ""}</div>
        <div class="fact"><div class="k">Take profit at</div><div class="v">${price(a.take_profit)}</div><div class="d up">${pct(a.take_profit / a.price - 1)}</div></div>
        <div class="fact"><div class="k">Safety exit at</div><div class="v">${price(a.safety_exit)}</div><div class="d down">${pct(a.safety_exit / a.price - 1)}</div></div>
        <div class="fact"><div class="k">Sell by</div>${market().open ? `<div class="v">${local(a.exit_by)}</div><div class="d muted">${nyTime(a.exit_by)} New York</div>` : `<div class="v">${esc(st.hold_text)}</div><div class="d muted">after you buy</div>`}</div>
      </div>
      <div class="chart" id="${esc(id)}"></div>
      <div class="chart-legend"><span><i class="l-base"></i>Price when suggested</span><span><i class="l-tp"></i>Take profit</span><span><i class="l-sl"></i>Safety exit</span><span>${state.style === "stk_today" ? "15-minute" : "1-hour"} candles, market hours</span></div>
      <div class="facts">
        <div class="fact"><div class="k">Beats the others</div><div class="v">${Math.round(d.p * 100)}%</div><div class="d muted">chance</div></div>
        <div class="fact"><div class="k">All costs, round trip</div><div class="v">${(d.cost * 100).toFixed(2)}%</div><div class="d muted">spread + fees${state.fx ? " + currency" : ""}</div></div>
        <div class="fact"><div class="k">Hold for at most</div><div class="v">${esc(st.hold_text || E.humanDuration(st.hold_minutes))}</div></div>
      </div>
      <div><h3>Why it was picked</h3><ul class="why">${d.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>
      ${warns.length ? `<div class="warnings">${warns.map((w) => `<div class="banner warn">⚠ ${esc(w)}</div>`).join("")}</div>` : ""}
      ${earn}
      <div class="calc" data-sym="${esc(d.symbol)}"><h3>Profit calculator (after spread, fees and currency conversion)</h3>
        <div class="row"><label class="inline">I put in $<input class="amt" type="number" min="1" step="any" value="${state.amount}"></label>
        <label class="inline">and sell at $<input class="tgt" type="number" min="0" step="any" placeholder="${a.take_profit.toFixed(2)}"></label></div>
        <div class="table-wrap"><table><tbody class="calc-out"></tbody></table></div></div>
      <div class="actions">${!market().open ? `<span class="pill warn">The market is closed: these ideas can only be bought once it opens, and will be re-ranked then.</span>` : state.trades.some((t) => t.symbol === d.symbol) ? `<span class="pill calm">You're watching this trade</span>` : `<button class="btn primary" type="button" data-take="${esc(d.symbol)}">I bought this: watch it for me</button>`}</div>
    </article>`;
  }
  function render() {
    const rows = (((state.snap.lists || {})[state.list] || {})[state.style] || []).map(evaluate);
    const pos = rows.filter((r) => r.r > 0).length; const share = rows.length ? pos / rows.length : 0;
    const label = share > 0.6 ? "Favourable" : share > 0.35 ? "Mixed" : "Unfavourable";
    const n = (state.snap.counts || {})[state.list] || 0;
    $("mood").className = `banner ${label === "Favourable" ? "good" : label === "Mixed" ? "calm" : "warn"}`;
    $("mood").innerHTML = rows.length ? `<b>Mood: ${label}.</b> ${pos} of the best ${rows.length} (out of ${n} ${state.list === "large" ? "large stocks" : "high-volatility stocks"} scanned) look positive after costs. ${label === "Unfavourable" ? "Doing nothing is a perfectly good choice." : "Scores above 5 beat break-even after costs."}`
      : `No ${state.list === "large" ? "large" : "high-volatility"} stock rankings yet for this speed. They appear after the first run during US market hours.`;
    dropCharts();
    $("ideas").innerHTML = `<div style="display:grid;gap:14px">${state.ideas.map(card).join("")}</div>`;
    for (const d of state.ideas) {
      const a = d.anchor; const c = drawChart($(`chart-${d.symbol}`), d.chart, [{ price: a.price, color: "--calm", dashed: true, title: "Suggested" },
        { price: a.take_profit, color: "--good", title: "Take profit" }, { price: a.safety_exit, color: "--bad", title: "Safety exit" }], a.at);
      if (c) state.charts[d.symbol] = c;
    }
    for (const el of document.querySelectorAll("#ideas .calc")) calcRows(el);
  }

  // ------------------------------------------------------------------ movers
  async function renderMovers() {
    const box = $("movers");
    try {
      const snap = await loadSnap(false);
      const lbl = { "large:stk_today": "Large · Today", "large:stk_days": "Large · Few days", "volatile:stk_today": "High volatility · Today", "volatile:stk_days": "High volatility · Few days" };
      box.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Stock</th><th class="num">Today</th><th class="num">Price at last update</th><th class="num">Traded</th><th style="min-width:18em">Why it is or isn't suggested</th></tr></thead><tbody>
        ${(snap.movers || []).map((m) => { const ins = Object.entries(m.in || {});
          const why = ins.length ? ins.map(([k, v]) => `${lbl[k]}: #${v.place}, score ${v.score.toFixed(1)}${v.warn ? `. ${esc(v.warn)}` : ""}`).join("<br>") : esc(m.why || "");
          return `<tr><td><b>${esc(m.symbol)}</b><div class="small muted">${esc(m.name)}</div></td><td class="num ${m.chg_pct >= 0 ? "up" : "down"}">${pct(m.chg_pct)}</td><td class="num">${price(m.price)}</td><td class="num">${big(m.dollar_vol)}</td><td class="small" style="white-space:normal">${why}</td></tr>`; }).join("")}
        </tbody></table></div><p class="small muted">From the update at ${new Date(Date.parse(snap.generated_at)).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}. Information only, not suggestions. Stocks that already jumped are often marked down: buying after a big jump often means buying the top.</p>`;
    } catch { box.innerHTML = `<div class="banner warn">Couldn't load the biggest movers yet.</div>`; }
  }

  // ------------------------------------------------------------------ trades
  function saveTrades() { store.set("omega.stk.trades", state.trades); const n = state.trades.length; $("tradeCount").hidden = n === 0; $("tradeCount").textContent = n; }
  async function renderTrades() {
    saveTrades(); const box = $("trades");
    if (!state.trades.length) { box.innerHTML = `<div class="banner calm">No stock trades yet. Tap “I bought this” on an idea.</div>`; return; }
    try { await loadSnap(false); } catch { /* use what we have */ }
    await livePrices(state.trades.map((t) => t.symbol));
    const all = Object.values(state.snap?.lists || {}).flatMap((by) => Object.values(by).flat());
    box.innerHTML = `<div style="display:grid;gap:14px">${state.trades.map((t) => {
      const row = all.find((r) => r.symbol === t.symbol); const mk = market(); const lp = livePx(t.symbol);
      const px = lp ?? (!mk.open && row ? row.price : null);
      const a = Number.isFinite(px) ? E.advise(t, px, Date.now(), null) : E.adviseAsOf(t, row?.price, snapWhen(), Date.now());
      if (!mk.open && a.action === "Time's up: sell now") { a.action = "Sell when the market opens"; a.why = "The time limit passed while the market was closed. Sell at the next open."; }
      notify(t, a);
      const v = Number.isFinite(px) ? outcome(t.amount, t.entry, px, { spread: t.spread }) : NaN;
      return `<article class="card trade ${a.level}"><div class="idea-head"><h2>${esc(t.symbol)}</h2><span class="verdict ${a.level}">${esc(a.action)}</span></div><p>${esc(a.why)}</p>
        <div class="strip"><div class="fact"><div class="k">You bought</div><div class="v">${localDay(t.opened)}</div><div class="d muted">at ${price(t.entry)}</div></div>
        <div class="fact"><div class="k">${lp || mk.open ? "Price now" : "Last close"}</div><div class="v">${price(px)}</div><div class="d ${!Number.isFinite(v) ? "muted" : v >= 0 ? "up" : "down"}">${Number.isFinite(v) ? `${usd(v)} on $${t.amount.toLocaleString()} after costs` : "not live: add a free Finnhub key above, or check your broker"}</div>${lp ? '<div class="small muted">Live (Finnhub)</div>' : ""}</div>
        <div class="fact"><div class="k">Take profit at</div><div class="v">${price(t.take_profit)}</div></div><div class="fact"><div class="k">Safety exit at</div><div class="v">${price(t.safety_exit)}</div></div>
        <div class="fact"><div class="k">Sell by</div><div class="v">${local(t.exit_by)}</div><div class="d muted">${nyTime(t.exit_by)} New York</div></div></div>
        <div class="actions"><button class="btn" type="button" data-sold="${esc(t.id)}">I've sold it: remove</button></div></article>`; }).join("")}</div>`;
  }
  function notify(t, a) {
    const prev = state.lastAction[t.id]; state.lastAction[t.id] = a.action;
    if (!prev || prev === a.action || a.action === "Hold" || a.action === "Waiting for a live price") return;
    document.title = `${a.action}: ${t.symbol} · Mempool Omega`;
    try { if ("Notification" in window && Notification.permission === "granted") new Notification(`${t.symbol}: ${a.action}`, { body: a.why }); } catch { /* not supported */ }
  }

  // ------------------------------------------------------------------ track record
  function parseCSV(text) { const lines = text.trim().split(/\r?\n/); const head = lines.shift().split(","); return lines.map((l) => { const v = l.split(","); return Object.fromEntries(head.map((h, i) => [h, v[i]])); }); }
  async function renderRecord() {
    let html = "";
    for (const [lk, name] of [["large", "Large stocks"], ["volatile", "High-volatility stocks"]]) {
      html += `<h2>${name}</h2>`;
      try {
        const b = await getJSON(REPO + `stocks/scoreboard_${lk}.json`);
        const rows = Object.entries(b.by_style || {}).filter(([, v]) => v.closed);
        if (!rows.length) { html += `<div class="banner calm">No ideas have reached their time limit yet.</div>`; continue; }
        html += `<div class="table-wrap"><table><thead><tr><th>Speed</th><th class="num">Ideas checked</th><th class="num">Ended in profit</th><th class="num">Average per idea</th><th class="num">Random pick average</th><th class="num">$100 in each idea</th></tr></thead><tbody>
          ${rows.map(([k, v]) => `<tr><td>${esc(state.snap?.styles[k]?.label || k)}</td><td class="num">${v.closed}</td><td class="num">${Math.round(v.win_rate * 100)}%</td><td class="num ${v.avg_return_per_idea >= 0 ? "up" : "down"}">${pct(v.avg_return_per_idea, 2)}</td>
            <td class="num">${pct(v.random_pick_avg_return, 2)} ${v.avg_return_per_idea > v.random_pick_avg_return ? '<span class="pill good">beating random</span>' : '<span class="pill warn">not beating random</span>'}</td><td class="num ${v.if_100usd_each_total_pnl >= 0 ? "up" : "down"}">${usd(v.if_100usd_each_total_pnl)}</td></tr>`).join("")}</tbody></table></div>`;
        try {
          const h = parseCSV(await getText(REPO + `stocks/history_${lk}.csv`)).filter((r) => r.status === "closed").slice(-10).reverse();
          if (h.length) html += `<div class="table-wrap"><table><tbody>${h.map((r) => `<tr><td>${esc(r.ts.slice(5, 16))}</td><td>${esc(r.symbol)}</td><td>${esc(r.outcome)}</td><td class="num ${+r.net_ret >= 0 ? "up" : "down"}">${pct(+r.net_ret, 2)}</td></tr>`).join("")}</tbody></table></div>`;
        } catch { /* optional */ }
      } catch { html += `<div class="banner calm">This track record starts after the first runs during US market hours.</div>`; }
    }
    $("record").innerHTML = html;
  }

  // ------------------------------------------------------------------ wiring
  function selectTab(name) {
    for (const b of document.querySelectorAll("nav.tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === name));
    for (const p of document.querySelectorAll("section.panel")) p.hidden = p.id !== `tab-${name}`;
    if (name === "movers") renderMovers(); if (name === "trades") renderTrades(); if (name === "record") renderRecord();
    store.set("omega.stk.tab", name); if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  }
  const press = (sel, attr, val) => { for (const b of document.querySelectorAll(sel)) b.setAttribute("aria-pressed", String(b.dataset[attr] === val)); };
  document.addEventListener("click", async (ev) => {
    const take = ev.target.closest("[data-take]");
    if (take) {
      const d = state.ideas.find((x) => x.symbol === take.dataset.take);
      if (d) {
        const amt = +take.closest("article").querySelector(".calc .amt")?.value || state.amount;
        // the price you actually paid: live if we have it, otherwise ask (the last update may be up to 15 minutes old)
        let px = livePx(d.symbol);
        if (!px) { const v = parseFloat(String(window.prompt(`What price did you pay per share of ${d.symbol}? (see your broker)`, "") || "").replace(/[$,\s]/g, "")); if (!(v > 0)) return; px = v; }
        const tr = E.makeTrade({ symbol: d.symbol, style: state.style, price_now: px, risk_unit: d.risk_unit, take_profit_pct: d.take_profit_pct, safety_exit_pct: d.safety_exit_pct, hold_minutes: state.snap.styles[state.style].hold_minutes });
        tr.exit_by = exitTime(Date.now()); Object.assign(tr, { amount: amt, spread: d.spread });
        state.trades.push(tr); saveTrades(); take.outerHTML = `<span class="pill good">Added at ${price(px)}. Open “My stock trades”.</span>`;
      }
    }
    const sold = ev.target.closest("[data-sold]");
    if (sold) { state.trades = state.trades.filter((t) => t.id !== sold.dataset.sold); renderTrades(); }
  });
  document.addEventListener("input", (ev) => { const c = ev.target.closest(".calc"); if (c) calcRows(c); });
  for (const b of document.querySelectorAll("#list button")) b.addEventListener("click", () => { state.list = b.dataset.list; store.set("omega.stk.list", state.list); press("#list button", "list", state.list); refresh(); });
  for (const b of document.querySelectorAll("#speed button")) b.addEventListener("click", () => { state.style = b.dataset.style; store.set("omega.stk.style", state.style); press("#speed button", "style", state.style); refresh(); });
  for (const b of document.querySelectorAll("nav.tabs button")) b.addEventListener("click", () => selectTab(b.dataset.tab));
  $("refresh").addEventListener("change", (e) => { state.refresh = +e.target.value; store.set("omega.stk.refresh", state.refresh); schedule(); });
  $("refreshNow").addEventListener("click", refresh);
  $("amount").addEventListener("change", (e) => { state.amount = Math.max(1, +e.target.value || 100); store.set("omega.stk.amount", state.amount); if (state.snap) render(); });
  $("fx").addEventListener("change", (e) => { state.fx = +e.target.value; store.set("omega.stk.fx", state.fx); refresh(); });
  $("fhSave").addEventListener("click", async () => {
    const k = $("fhKey").value.trim(); if (!k) return;
    try { const q = await getJSON(`https://finnhub.io/api/v1/quote?symbol=AAPL&token=${encodeURIComponent(k)}`); if (!(q.c > 0)) throw new Error("no price");
      state.key = k; store.set("omega.stk.finnhub", k); $("fhMsg").textContent = "Saved. Live prices are on."; $("fhKey").value = ""; refresh();
    } catch { $("fhMsg").textContent = "That key didn't work. Copy it again from your Finnhub dashboard."; }
  });
  $("fhClear").addEventListener("click", () => { state.key = ""; store.set("omega.stk.finnhub", ""); state.live = {}; $("fhMsg").textContent = "Removed."; refresh(); });
  $("notifyBtn").addEventListener("click", async () => { if (!("Notification" in window)) return; const p = await Notification.requestPermission(); $("notifyState").textContent = p === "granted" ? "Alerts are on while this page is open." : "Alerts are off."; });
  window.addEventListener("omega-theme", () => { if (state.snap) render(); });

  // ------------------------------------------------------------------ start
  $("refresh").value = String(state.refresh); $("amount").value = String(state.amount); $("fx").value = String(state.fx);
  if (state.key) $("fhMsg").textContent = "Live prices are on (key saved in this browser).";
  press("#list button", "list", state.list); press("#speed button", "style", state.style);
  const startTab = (location.hash || "").slice(1) || store.get("omega.stk.tab", "ideas");
  selectTab(["ideas", "movers", "trades", "record", "guide"].includes(startTab) ? startTab : "ideas");
  saveTrades(); paintMarket(); refresh();
  setInterval(() => { paintStatus(); paintMarket(); }, 1000 * 15);
  setInterval(paintStatus, 1000);
  setInterval(() => {      // a live price that stopped being confirmed is blanked out, not left on screen
    if (!market().open) return;
    for (const el of document.querySelectorAll("#ideas [data-live]")) if (!livePx(el.dataset.live) && el.textContent !== "–") {
      el.textContent = "–";
      for (const s2 of document.querySelectorAll(`[data-since="${el.dataset.live}"]`)) { s2.textContent = "not live right now: check your broker"; s2.className = "d muted"; }
      for (const c of document.querySelectorAll(`#ideas .calc[data-sym="${el.dataset.live}"]`)) calcRows(c);
    }
  }, 5000);
  setInterval(async () => {   // live price ticks (Finnhub key) for the ideas and trades
    if (!state.key || document.hidden || !state.snap) return;
    await livePrices(state.ideas.map((d) => d.symbol).concat(state.trades.map((t) => t.symbol)));
    for (const d of state.ideas) { const lp = livePx(d.symbol); if (!lp) continue; for (const el of document.querySelectorAll(`[data-live="${d.symbol}"]`)) el.textContent = price(lp); }
    for (const el of document.querySelectorAll("#ideas .calc")) calcRows(el);
    if (!$("tab-trades").hidden) renderTrades();
  }, 30000);
})();
