/* Mempool Omega Forex page. Rankings and manipulation checks: the project's 15-minute snapshot (GitHub Actions
   reads Yahoo and the ForexFactory calendar, which web pages can't read directly). Live prices for the ideas and
   your trades: Coinbase's public exchange rates (every currency in one request, no key) and, for gold, Binance's
   PAXG (a gold-backed token that tracks one ounce). */
(function () {
  "use strict";
  const E = window.OmegaEngine, Q = window.OmegaQuality;
  const qual = () => (state.snap && state.snap.quality) || null;   // grade check + jumpiness adjustment (applied on the server)
  const DATA = window.OMEGA_FX_DATA || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/data/fx/";
  const REPO = window.OMEGA_REPO || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/main/state/";
  const GRADES = [[6.5, "Strong"], [5.6, "Moderate"], [5.0, "Weak"], [-1, "Avoid - watch only"]];
  const LIVE_EVERY = 60e3;   // live prices once a minute (Coinbase updates its rates about once a minute)
  const LIVE_MAX_GAP = { major: 0.015, cross: 0.015, metal: 0.02, exotic: 0.05 };   // ignore a live price this far from the snapshot
  const STOP_OUT = 0.5;      // most brokers close trades when losses reach ~50% of the money put up
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
  };
  const state = {
    list: store.get("omega.fx.list", "main"), style: store.get("omega.fx.style", "fx_today"), refresh: store.get("omega.fx.refresh", 300),
    amount: store.get("omega.fx.amount", 100), lev: store.get("omega.fx.lev", 1), snap: null, ideas: [], timer: null, nextAt: 0, busy: false,
    trades: store.get("omega.fx.trades", []), anchors: store.get("omega.fx.sugg", {}), charts: {}, lastAction: {},
    live: { rates: null, gold: null, silver: null, at: 0 },
  };
  const dp = (pair, x) => (!Number.isFinite(x) ? "–" : x.toFixed(pair.endsWith("JPY") ? 3 : x >= 100 ? 2 : x >= 10 ? 4 : 5));
  const pct = (x, d = 2) => (Number.isFinite(x) ? `${x >= 0 ? "+" : ""}${(x * 100).toFixed(d)}%` : "–");
  const usd = (x) => (Number.isFinite(x) ? `${x >= 0 ? "+" : "−"}$${Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "–");
  const local = (ms) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const localDay = (ms) => new Date(ms).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
  const gradeOf = (s) => GRADES.find(([th]) => s >= th)[1];
  const gradeClass = (g) => (g === "Strong" ? "good" : g === "Moderate" ? "calm" : g === "Weak" ? "warn" : "bad");
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const key = (d) => `${d.pair}:${d.side}`;
  async function getJSON(url) { const r = await fetch(url, { cache: "no-store" }); if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); }
  async function getText(url) { const r = await fetch(url, { cache: "no-store" }); if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.text(); }

  // ------------------------------------------------------------------ live prices
  async function pollLive() {
    if (document.hidden || !marketOpen()) return;
    const need = (pair) => [...state.ideas, ...state.trades].some((d) => d.pair === pair);
    const [cb, gold, silver] = await Promise.allSettled([
      getJSON("https://api.coinbase.com/v2/exchange-rates?currency=USD"),
      need("XAUUSD") ? metalQuote("XAU") : Promise.resolve(null),
      need("XAGUSD") ? metalQuote("XAG") : Promise.resolve(null)]);
    if (cb.status === "fulfilled" && cb.value?.data?.rates) { state.live.rates = cb.value.data.rates; state.live.at = Date.now(); }
    state.live.gold = gold.status === "fulfilled" && gold.value > 0 ? gold.value : null;          // a failed poll never leaves an old price
    state.live.silver = silver.status === "fulfilled" && silver.value > 0 ? silver.value : null;
    paintLive(); checkTrades();
  }
  // gold and silver: the round-the-clock contracts on three exchanges, the middle of the quotes that answer (one bad quote
  // can't move it). They sit a little below the futures prices the ideas are built on, so livePrice() adds the gap the
  // 15-minute job measured on matching candles (snapshot "metal_basis").
  async function metalQuote(m) {
    const qs = await Promise.allSettled([
      getJSON(`https://api.bitget.com/api/v2/mix/market/ticker?productType=USDT-FUTURES&symbol=${m}USDT`).then((j) => +j?.data?.[0]?.lastPr),
      getJSON(`https://api.gateio.ws/api/v4/futures/usdt/tickers?contract=${m}_USDT`).then((j) => +j?.[0]?.last),
      getJSON(`https://www.okx.com/api/v5/market/ticker?instId=${m}-USDT-SWAP`).then((j) => +j?.data?.[0]?.last)]);
    const v = qs.filter((x) => x.status === "fulfilled" && x.value > 0).map((x) => x.value).sort((a, b) => a - b);
    return !v.length ? null : v.length % 2 ? v[(v.length - 1) / 2] : (v[v.length / 2 - 1] + v[v.length / 2]) / 2;
  }
  // pair price from rates per 1 USD: EUR/JPY = JPY per USD / EUR per USD. `ref` is the last snapshot price: a live price that
  // disagrees with it by more than a pair-type limit is treated as a bad quote and not used (the same idea as the triangular check).
  function livePrice(pair, ref, kind) {
    const r = state.live.rates; if (!r || Date.now() - state.live.at > 150e3) return null;   // one missed poll at most
    const b = pair.slice(0, 3), q = pair.slice(3); let px = null;
    if ((b === "XAU" || b === "XAG") && q === "USD") {
      const basis = state.snap?.metal_basis?.[pair]?.basis;          // no measured gap: no live price (never a mismatched one)
      const raw = b === "XAU" ? state.live.gold : state.live.silver;
      px = basis > 0 && raw > 0 ? raw * basis : null;
    } else { const rb = b === "USD" ? 1 : +r[b], rq = q === "USD" ? 1 : +r[q]; if (rb > 0 && rq > 0) px = rq / rb; }
    if (!Number.isFinite(px) || px <= 0) return null;
    const lim = LIVE_MAX_GAP[kind] ?? (b === "XAU" || b === "XAG" ? LIVE_MAX_GAP.metal : LIVE_MAX_GAP.cross);
    return Number.isFinite(ref) && Math.abs(px / ref - 1) > lim ? null : px;
  }
  // live only: the 15-minute snapshot price is never shown or used as "now"
  const priceOf = (d) => { const lp = livePrice(d.pair, d.price, d.kind); return lp ? { px: lp, live: true } : { px: NaN, live: false }; };
  const snapWhen = () => `${new Date(Date.parse(state.snap.generated_at)).toISOString().slice(11, 16)} UTC`;
  function paintLive() {
    for (const d of state.ideas) {
      const el = document.querySelector(`[data-live="${CSS.escape(key(d))}"]`); if (!el) continue;
      const { px, live } = priceOf(d); const a = d.anchor;
      const move = d.side === "buy" ? px / a.price - 1 : a.price / px - 1;
      el.innerHTML = liveFact(d.pair, px, live, move);
      const c = document.querySelector(`[data-calc="${CSS.escape(key(d))}"]`); if (c) c.outerHTML = calcHTML(d);
    }
    if (!$("tab-trades").hidden) renderTrades(false);
  }
  function liveFact(pair, px, live, move) {
    return `<div class="k">Price now${live ? " (live)" : ""}</div><div class="v">${live ? dp(pair, px) : "–"}</div>` +
      `<div class="d ${live ? (move >= 0 ? "up" : "down") : "muted"}">${live ? `${pct(move)} for this idea since suggested` : marketOpen() ? "not live right now: check your broker" : "market closed"}</div>`;
  }

  // ------------------------------------------------------------------ market hours & sessions
  function marketOpen() {
    const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(new Date()).map((x) => [x.type, x.value]));
    const mins = (+p.hour % 24) * 60 + +p.minute;
    return !(p.weekday === "Sat" || (p.weekday === "Fri" && mins >= 1020) || (p.weekday === "Sun" && mins < 1020));
  }
  function sessions() {
    const s = [["Sydney", "Australia/Sydney", 7, 16], ["Tokyo", "Asia/Tokyo", 9, 18], ["London", "Europe/London", 8, 17], ["New York", "America/New_York", 8, 17]];
    return s.filter(([, tz, a, b]) => { const h = +new Intl.DateTimeFormat("en-US", { timeZone: tz, hour: "2-digit", hour12: false }).format(new Date()) % 24; return h >= a && h < b; }).map((x) => x[0]);
  }
  function paintMarket() {
    $("clock").textContent = `UTC ${new Date().toISOString().slice(11, 16)}`;
    const b = $("market"); const open = marketOpen(); const ss = sessions();
    if (!open) { b.className = "banner warn"; b.innerHTML = "<b>Forex market is closed for the weekend.</b> It reopens Sunday 5 pm New York time. Ideas below are from the last update."; return; }
    const thin = state.snap?.thin;
    b.className = `banner ${thin ? "warn" : "good"}`;
    b.innerHTML = `<b>Forex market is open.</b> Active sessions: ${ss.length ? ss.join(", ") : "between sessions (quiet)"}.${thin ? ` ⚠ ${esc(thin)}` : ""}`;
  }

  // ------------------------------------------------------------------ ranking (costs depend on nothing user-specific except leverage, shown in the calculator)
  function evaluate(d) {
    const sc = E.scoreFromR(d.expected_r);
    return { ...d, scoreNow: sc, gradeNow: gradeOf(sc) };
  }
  async function refresh() {
    if (state.busy) return;
    state.busy = true; $("refreshNow").disabled = true; setStatus("Loading the latest forex rankings…");
    try {
      state.snap = await getJSON(DATA + "snapshot.json");
      const rows = ((state.snap.lists || {})[state.list] || {})[state.style] || [];
      state.ideas = rows.filter((d) => !d.hard.length).map(evaluate).sort((a, b) => b.scoreNow - a.scoreNow).slice(0, 5).map((d, i) => ({ ...d, rank: i + 1 }));
      state.blocked = rows.filter((d) => d.hard.length);
      anchor(); render();
      // a gold or silver idea just appeared without its live price yet: fetch it now rather than at the next minute
      if ((state.ideas.some((d) => d.pair === "XAUUSD") && !state.live.gold) || (state.ideas.some((d) => d.pair === "XAGUSD") && !state.live.silver)) pollLive();
      setStatus(`Rankings from ${Math.round((Date.now() - Date.parse(state.snap.generated_at)) / 60000)} min ago · they update every 15 minutes · live prices every minute`);
    } catch (e) {
      $("mood").className = "banner bad"; $("mood").textContent = `Couldn't load the forex rankings yet (${e.message || e}).`; $("ideas").innerHTML = ""; setStatus("Couldn't load data");
    } finally { state.busy = false; $("refreshNow").disabled = false; schedule(); paintMarket(); }
  }
  function schedule() { clearTimeout(state.timer); if (state.refresh > 0) { state.nextAt = Date.now() + state.refresh * 1000; state.timer = setTimeout(refresh, state.refresh * 1000); } else state.nextAt = 0; }
  function setStatus(t) { $("status").dataset.base = t; paintStatus(); }
  function paintStatus() { let x = ""; if (state.nextAt && !state.busy) { const s = Math.max(0, Math.round((state.nextAt - Date.now()) / 1000)); x = ` · next in ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; } $("status").textContent = ($("status").dataset.base || "") + x; }
  function anchor() {
    const now = Date.now(); const keep = {};
    for (const d of state.ideas) {
      const k = `${state.list}:${state.style}:${key(d)}`; let a = state.anchors[k];
      // exits come from the snapshot price, so the suggestion is dated at the snapshot, not "now"
      if (!a || now > a.exit_by) a = { at: Date.parse(state.snap.generated_at) || now, price: d.price, take_profit: d.take_profit, safety_exit: d.safety_exit, exit_by: Date.parse(d.sell_by) };
      keep[k] = a; d.anchor = a;
    }
    for (const [k, a] of Object.entries(state.anchors)) if (!k.startsWith(`${state.list}:${state.style}:`) && now < a.exit_by + 864e5) keep[k] = a;
    state.anchors = keep; store.set("omega.fx.sugg", keep);
  }

  // money result for `amount` of your own money at `lev` leverage, from entry to exit, after the spread and overnight fees
  function outcome(d, amount, lev, entry, exit) {
    const pos = amount * lev; const move = d.side === "buy" ? exit / entry - 1 : entry / exit - 1;
    return pos * move - pos * d.cost;
  }

  // ------------------------------------------------------------------ cards
  function drawChart(el, ch, lines) {
    if (!el || !ch || !window.LightweightCharts) return null;
    const chart = LightweightCharts.createChart(el, { autoSize: true, layout: { background: { color: cssVar("--surface") }, textColor: cssVar("--muted"), fontFamily: cssVar("--f-num") },
      grid: { vertLines: { color: cssVar("--sunken") }, horzLines: { color: cssVar("--sunken") } }, rightPriceScale: { borderColor: cssVar("--line") },
      timeScale: { borderColor: cssVar("--line"), timeVisible: true, secondsVisible: false }, crosshair: { mode: 0 }, handleScroll: false, handleScale: false });
    const prec = ch.close[ch.close.length - 1] >= 100 ? 2 : ch.close[ch.close.length - 1] >= 10 ? 3 : 5;
    const s = chart.addCandlestickSeries({ upColor: cssVar("--good"), downColor: cssVar("--bad"), borderVisible: false, wickUpColor: cssVar("--good"), wickDownColor: cssVar("--bad"), priceFormat: { type: "price", precision: prec, minMove: Math.pow(10, -prec) } });
    s.setData(ch.t.map((t, i) => ({ time: t, open: ch.open[i], high: ch.high[i], low: ch.low[i], close: ch.close[i] })));
    for (const l of lines) s.createPriceLine({ price: l.price, color: cssVar(l.color), lineWidth: 2, lineStyle: l.dashed ? 2 : 0, axisLabelVisible: true, title: l.title });
    chart.timeScale().fitContent();
    return { chart };
  }
  function calcHTML(d) {
    const a = d.anchor; const amt = state.amount; const lev = state.lev; const pos = amt * lev;
    const stopOutMove = STOP_OUT / lev; const slDist = Math.abs(a.safety_exit / a.price - 1);
    const row = (label, x) => { const v = outcome(d, amt, lev, a.price, x); return `<tr><td>${label}</td><td class="num">${dp(d.pair, x)}</td><td class="num ${v >= 0 ? "up" : "down"}">${usd(v)}</td><td class="num muted">${pct(v / amt, 1)}</td></tr>`; };
    return `<div class="calc" data-calc="${esc(key(d))}"><h3>Profit calculator: $${amt.toLocaleString()} of your money, ${lev === 1 ? "no leverage" : `${lev}x leverage`} (a $${pos.toLocaleString()} position)</h3>
      <div class="table-wrap"><table><tbody>${row("If it reaches the take profit", a.take_profit)}${row("If it hits the safety exit", a.safety_exit)}${(() => { const { px, live } = priceOf(d); return live ? row("If you closed right away (live price)", px) : `<tr><td>If you closed right away</td><td colspan="3" class="muted">shown with a live price</td></tr>`; })()}</tbody></table></div>
      ${lev > 1 ? `<div class="banner ${slDist >= stopOutMove ? "bad" : "warn"}">At ${lev}x, a move of about ${pct(stopOutMove, 1).replace("+", "")} against you loses half your money and most brokers close the trade (margin call).${slDist >= stopOutMove ? " That's <b>before</b> your safety exit: lower the leverage." : ""}</div>` : ""}
      <p class="small muted">After the spread (${pct(d.spread, 3).replace("+", "")})${d.swap_nights ? ` and about ${d.swap_nights} night(s) of overnight fees` : ""}. Change "Your money" and "Leverage" at the top.</p></div>`;
  }
  function card(d) {
    const a = d.anchor; const id = `chart-${key(d).replace(":", "-")}`;
    const failed = d.checks.filter((c) => c.ok === false), passed = d.checks.filter((c) => c.ok === true);
    return `<article class="card" data-alert-key="idea-${esc(state.style)}-${esc(d.pair)}-${esc(d.side)}">
      <div class="idea-head"><h2><span class="rank">${d.rank}.</span><span class="pill ${d.side === "buy" ? "good" : "bad"}" style="font-size:1rem">${d.side === "buy" ? "BUY" : "SELL"}</span> ${esc(d.label)}</h2>
        <div class="score"><b>${d.scoreNow.toFixed(1)}</b><span class="muted">/ 10</span><span class="pill ${Q.gradeClassFor(d.gradeNow, qual())}">${esc(Q.gradeWord(d.gradeNow, qual(), d.rank))}</span></div></div>
      <p><b>${esc(d.headline)}</b></p>
      <div class="strip">
        <div class="fact"><div class="k">Suggested</div><div class="v">${localDay(a.at)}</div><div class="d muted">at ${dp(d.pair, a.price)}</div></div>
        <div class="fact" data-live="${esc(key(d))}">${(() => { const { px, live } = priceOf(d); return liveFact(d.pair, px, live, d.side === "buy" ? px / a.price - 1 : a.price / px - 1); })()}</div>
        <div class="fact"><div class="k">Take profit at</div><div class="v">${dp(d.pair, a.take_profit)}</div><div class="d up">${pct(Math.abs(a.take_profit / a.price - 1))} in your favour</div></div>
        <div class="fact"><div class="k">Safety exit at</div><div class="v">${dp(d.pair, a.safety_exit)}</div><div class="d down">${pct(-Math.abs(a.safety_exit / a.price - 1))} against you</div></div>
        <div class="fact"><div class="k">Close by</div><div class="v">${local(a.exit_by)}</div><div class="d muted">${new Date(a.exit_by).toISOString().slice(11, 16)} UTC</div></div>
      </div>
      <div class="chart" id="${esc(id)}"></div>
      <div class="chart-legend"><span><i class="l-base"></i>Price when suggested</span><span><i class="l-tp"></i>Take profit</span><span><i class="l-sl"></i>Safety exit</span><span>Pair price, ${state.style === "fx_today" ? "15-minute" : "1-hour"} candles</span></div>
      <div class="facts"><div class="fact"><div class="k">Beats the others</div><div class="v">${Math.round(d.p * 100)}%</div><div class="d muted">chance</div></div>
        <div class="fact"><div class="k">Costs</div><div class="v">${pct(d.cost, 3).replace("+", "")}</div><div class="d muted">of the position</div></div>
        <div class="fact"><div class="k">Hold for at most</div><div class="v">${esc(state.snap.styles[state.style].hold_text)}</div></div></div>
      <div><h3>Why it was picked</h3><ul class="why">${d.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>
      ${d.warnings.length ? `<div class="warnings">${d.warnings.map((w) => `<div class="banner warn">⚠ ${esc(w)}</div>`).join("")}</div>` : ""}
      <details class="checks"><summary>Manipulation checks: <span class="pill ${failed.length ? "warn" : "good"}">${passed.length} clean${failed.length ? `, ${failed.length} warning${failed.length > 1 ? "s" : ""}` : ""}</span></summary>
        <ul>${d.checks.map((c) => `<li>${c.ok ? '<span class="ok">✓</span>' : c.ok === false ? '<span class="bad">✗</span>' : '<span class="na">–</span>'}<span>${esc(c.text)}</span></li>`).join("")}</ul></details>
      ${calcHTML(d)}
      <div class="actions">${!marketOpen() ? `<span class="pill warn">The market is closed for the weekend.</span>` : state.trades.some((t) => t.key === key(d)) ? `<span class="pill calm">You're watching this trade</span>` : `<button class="btn primary" type="button" data-take="${esc(key(d))}">I took this trade: watch it for me</button>`}</div>
    </article>`;
  }
  function render() {
    const rows = (((state.snap.lists || {})[state.list] || {})[state.style] || []).filter((d) => !d.hard.length);
    const pos = rows.filter((d) => d.expected_r > 0).length; const share = rows.length ? pos / rows.length : 0;
    const label = share > 0.6 ? "Favourable" : share > 0.35 ? "Mixed" : "Unfavourable";
    $("mood").className = `banner ${state.list === "exotic" ? "warn" : label === "Favourable" ? "good" : label === "Mixed" ? "calm" : "warn"}`;
    $("mood").innerHTML = (state.list === "exotic" ? "<b>Exotic pairs: information only.</b> Wide spreads and central-bank-managed rates make these hard to trade profitably. " : "") +
      `<b>Mood: ${label}.</b> ${pos} of the best ${rows.length} pair-and-direction ideas look positive after costs.` +
      (state.blocked?.length ? ` ${state.blocked.length} held back right now (news about to hit, or a price that disagrees with related pairs).` : "");
    for (const c of Object.values(state.charts)) { try { c.chart.remove(); } catch { /* gone */ } } state.charts = {};
    $("ideas").innerHTML = `${Q.ideasBanner(qual())}<div style="display:grid;gap:14px">${state.ideas.map(card).join("")}</div>`;
    for (const d of state.ideas) {
      const a = d.anchor; const c = drawChart($(`chart-${key(d).replace(":", "-")}`), d.chart, [{ price: a.price, color: "--calm", dashed: true, title: "Suggested" },
        { price: a.take_profit, color: "--good", title: "Take profit" }, { price: a.safety_exit, color: "--bad", title: "Safety exit" }]);
      if (c) state.charts[key(d)] = c;
    }
  }

  // ------------------------------------------------------------------ other tabs
  async function renderNews() {
    try {
      if (!state.snap) state.snap = await getJSON(DATA + "snapshot.json");
      const ev = state.snap.events || [];
      $("news").innerHTML = `<h3>Coming up: high-impact announcements</h3>${ev.length ? `<div class="table-wrap"><table><thead><tr><th>When (your time)</th><th>Currency</th><th>What</th></tr></thead><tbody>${ev.map((e) => `<tr><td>${localDay(Date.parse(e.at))}</td><td>${esc(e.country)}</td><td>${esc(e.title)}</td></tr>`).join("")}</tbody></table></div>` : `<p class="muted">None in the calendar right now.</p>`}
        <h3>Next rate-fixing windows (no entries or exits inside)</h3><div class="table-wrap"><table><tbody>${(state.snap.fix_windows || []).map((f) => `<tr><td>${esc(f.name)}</td><td>${localDay(Date.parse(f.start))} – ${local(Date.parse(f.end))}</td></tr>`).join("")}</tbody></table></div>`;
    } catch { $("news").innerHTML = `<div class="banner warn">Couldn't load the calendar.</div>`; }
  }
  async function renderMovers() {
    try {
      if (!state.snap) state.snap = await getJSON(DATA + "snapshot.json");
      $("movers").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Pair</th><th>Type</th><th class="num">24 h change</th></tr></thead><tbody>${(state.snap.movers || []).map((m) => `<tr><td><b>${esc(m.label)}</b></td><td>${esc(m.kind)}</td><td class="num ${m.chg >= 0 ? "up" : "down"}">${pct(m.chg)}</td></tr>`).join("")}</tbody></table></div><p class="small muted">Information only. A pair that just moved a lot is not a reason to trade it.</p>`;
    } catch { $("movers").innerHTML = `<div class="banner warn">Couldn't load the movers.</div>`; }
  }
  function saveTrades() { store.set("omega.fx.trades", state.trades); $("tradeCount").hidden = !state.trades.length; $("tradeCount").textContent = state.trades.length; }
  // the price to judge a trade by: live if available, else the latest snapshot price, else the entry
  function tradeView(t) {
    const all = Object.values(state.snap?.lists || {}).flatMap((by) => Object.values(by).flat());
    const row = all.find((r) => key(r) === t.key); const ref = row ? row.price : t.last || t.entry;
    const lp = livePrice(t.pair, ref, row?.kind);
    if (row) t.last = row.price;
    // advise() thinks in "price up = good": give it the side-adjusted view
    const view = (x) => (t.side === "buy" ? x : 1 / x);
    const tv = { entry: view(t.entry), take_profit: view(t.take_profit), safety_exit: view(t.safety_exit), exit_by: t.exit_by };
    const a = lp ? E.advise(tv, view(lp), Date.now(), null) : E.adviseAsOf(tv, row ? view(row.price) : NaN, state.snap ? snapWhen() : "the last update", Date.now());
    return { px: lp || NaN, live: !!lp, a };
  }
  function checkTrades() { for (const t of state.trades) notify(t, tradeView(t).a); }
  async function renderTrades(reload = true) {
    saveTrades();
    if (!state.trades.length) { $("trades").innerHTML = `<div class="banner calm">No forex trades yet. Tap “I took this trade” on an idea.</div>`; return; }
    if (reload) { try { state.snap = await getJSON(DATA + "snapshot.json"); } catch { /* keep */ } }
    $("trades").innerHTML = `<div style="display:grid;gap:14px">${state.trades.map((t) => {
      const { px, live, a } = tradeView(t);
      notify(t, a);
      const v = live ? outcome(t, t.amount, t.lev, t.entry, px) : NaN;
      return `<article class="card trade ${a.level}" data-alert-key="trade-${esc(t.id)}"><div class="idea-head"><h2>${t.side.toUpperCase()} ${esc(t.label)}</h2><span class="verdict ${a.level}">${esc(a.action)}</span></div><p>${esc(a.why)}</p>
        <div class="strip"><div class="fact"><div class="k">Opened</div><div class="v">${localDay(t.opened)}</div><div class="d muted">at ${dp(t.pair, t.entry)}</div></div>
        <div class="fact"><div class="k">Price now${live ? " (live)" : ""}</div><div class="v">${dp(t.pair, px)}</div><div class="d ${!live ? "muted" : v >= 0 ? "up" : "down"}">${live ? `${usd(v)} on $${t.amount} at ${t.lev}x` : "not live right now: check your broker"}</div></div>
        <div class="fact"><div class="k">Take profit</div><div class="v">${dp(t.pair, t.take_profit)}</div></div><div class="fact"><div class="k">Safety exit</div><div class="v">${dp(t.pair, t.safety_exit)}</div></div>
        <div class="fact"><div class="k">Close by</div><div class="v">${local(t.exit_by)}</div></div></div>
        <div class="actions"><button class="btn" type="button" data-sold="${esc(t.id)}">I've closed it: remove</button></div></article>`; }).join("")}</div><p class="small muted">Live prices are checked every minute while this page is open (Coinbase's reference rates; gold from PAXG). Your broker's price can differ slightly, and silver updates every 15 minutes.</p>`;
  }
  function notify(t, a) {
    const prev = state.lastAction[t.id]; state.lastAction[t.id] = a.action; if (window.OmegaAlerts) window.OmegaAlerts.trade(window.OmegaAlerts.page, t, a); if (!prev || prev === a.action || a.action === "Hold" || a.action === "Waiting for a live price") return;
    document.title = `${a.action}: ${t.label} · Mempool Omega`;
    try { if (!window.OmegaAlerts && "Notification" in window && Notification.permission === "granted") new Notification(`${t.label}: ${a.action}`, { body: a.why }); } catch { /* not supported */ }
  }
  function parseCSV(text) { const lines = text.trim().split(/\r?\n/); const head = lines.shift().split(","); return lines.map((l) => { const v = l.split(","); return Object.fromEntries(head.map((h, i) => [h, v[i]])); }); }
  async function renderRecord() {
    try {
      const b = await getJSON(REPO + "fx/scoreboard.json");
      const rows = Object.entries(b.by_style || {}).filter(([, v]) => v.closed);
      let html = rows.length ? `<div class="table-wrap"><table><thead><tr><th>Speed</th><th class="num">Ideas checked</th><th class="num">Ended in profit</th><th class="num">Average per idea</th><th class="num">Random pick average</th></tr></thead><tbody>${rows.map(([k, v]) => `<tr><td>${esc(state.snap?.styles[k]?.label || k)}</td><td class="num">${v.closed}</td><td class="num">${Math.round(v.win_rate * 100)}%</td><td class="num ${v.avg_return_per_idea >= 0 ? "up" : "down"}">${pct(v.avg_return_per_idea, 3)}</td><td class="num">${pct(v.random_pick_avg_return, 3)} ${v.avg_return_per_idea > v.random_pick_avg_return ? '<span class="pill good">beating random</span>' : '<span class="pill warn">not beating random</span>'}</td></tr>`).join("")}</tbody></table></div>` : `<div class="banner calm">No ideas have reached their time limit yet.</div>`;
      html += Q.gradeTable(b.quality);
      try { const h = parseCSV(await getText(REPO + "fx/history.csv")).filter((r) => r.status === "closed").slice(-12).reverse(); if (h.length) html += `<div class="table-wrap"><table><tbody>${h.map((r) => `<tr><td>${esc(r.ts.slice(5, 16))}</td><td>${esc(r.symbol.replace(":", " ").toUpperCase())}</td><td>${esc(r.outcome)}</td><td class="num ${+r.net_ret >= 0 ? "up" : "down"}">${pct(+r.net_ret, 3)}</td></tr>`).join("")}</tbody></table></div>`; } catch { /* optional */ }
      $("record").innerHTML = html;
    } catch { $("record").innerHTML = `<div class="banner calm">The forex track record starts after the first hourly runs.</div>`; }
  }

  // ------------------------------------------------------------------ wiring
  function selectTab(name) {
    for (const b of document.querySelectorAll("nav.tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === name));
    for (const p of document.querySelectorAll("section.panel")) p.hidden = p.id !== `tab-${name}`;
    if (name === "news") renderNews(); if (name === "movers") renderMovers(); if (name === "trades") renderTrades(); if (name === "record") renderRecord();
    store.set("omega.fx.tab", name); if (location.hash !== `#/${name}`) history.replaceState(null, "", `#/${name}`);
  }
  const press = (sel, attr, val) => { for (const b of document.querySelectorAll(sel)) b.setAttribute("aria-pressed", String(b.dataset[attr] === val)); };
  document.addEventListener("click", (ev) => {
    const take = ev.target.closest("[data-take]");
    if (take) {
      const d = state.ideas.find((x) => key(x) === take.dataset.take);
      if (d) {
        const a = d.anchor; const now = Date.now(); let entry = priceOf(d).px;
        // the price you actually got: live if we have it, otherwise ask (the last update may be up to 15 minutes old)
        // no live price this minute (market closed, or every source failed): offer the last known price, to change if needed
        if (!(entry > 0)) { const v = parseFloat(String(window.prompt(`No live price for ${d.label} right now. The last price we have is below (from up to 15 minutes ago). Change it to the price your broker filled you at, then press OK.`, String(d.price ?? "")) || "").replace(/[,\s]/g, "")); if (!(v > 0)) return; entry = v; }
        state.trades.push({ id: `${key(d)}-${now}`, key: key(d), pair: d.pair, label: d.label, side: d.side, entry, take_profit: a.take_profit, safety_exit: a.safety_exit,
          exit_by: Date.parse(d.sell_by), opened: now, amount: state.amount, lev: state.lev, cost: d.cost });
        saveTrades(); take.outerHTML = `<span class="pill good">Added at ${dp(d.pair, entry)}. Open “My forex trades”.</span>`;
      }
    }
    const sold = ev.target.closest("[data-sold]");
    if (sold) { state.trades = state.trades.filter((t) => t.id !== sold.dataset.sold); renderTrades(); }
  });
  for (const b of document.querySelectorAll("#list button")) b.addEventListener("click", () => { state.list = b.dataset.list; store.set("omega.fx.list", state.list); press("#list button", "list", state.list); refresh(); });
  for (const b of document.querySelectorAll("#speed button")) b.addEventListener("click", () => { state.style = b.dataset.style; store.set("omega.fx.style", state.style); press("#speed button", "style", state.style); refresh(); });
  for (const b of document.querySelectorAll("nav.tabs button")) b.addEventListener("click", () => selectTab(b.dataset.tab));
  $("refresh").addEventListener("change", (e) => { state.refresh = +e.target.value; store.set("omega.fx.refresh", state.refresh); schedule(); });
  $("refreshNow").addEventListener("click", refresh);
  $("amount").addEventListener("change", (e) => { state.amount = Math.max(1, +e.target.value || 100); store.set("omega.fx.amount", state.amount); if (state.snap) render(); });
  $("lev").addEventListener("change", (e) => { state.lev = +e.target.value; store.set("omega.fx.lev", state.lev); if (state.snap) render(); });
  $("notifyBtn").addEventListener("click", async () => { if (!("Notification" in window)) return; const p = await Notification.requestPermission(); $("notifyState").textContent = p === "granted" ? "Alerts are on while this page is open." : "Alerts are off."; });
  window.addEventListener("omega-theme", () => { if (state.snap) render(); });

  $("refresh").value = String(state.refresh); $("amount").value = String(state.amount); $("lev").value = String(state.lev);
  press("#list button", "list", state.list); press("#speed button", "style", state.style);
  const startTab = (location.hash || "").replace(/^#\/?/, "") || store.get("omega.fx.tab", "ideas");
  selectTab(["ideas", "news", "movers", "trades", "record", "guide"].includes(startTab) ? startTab : "ideas");
  saveTrades(); paintMarket(); refresh();
  setInterval(paintStatus, 1000); setInterval(paintMarket, 30000);
  pollLive(); setInterval(pollLive, LIVE_EVERY);
  setInterval(paintLive, 15000);   // blanks out a live price once it is no longer confirmed
  document.addEventListener("visibilitychange", () => { if (!document.hidden && Date.now() - state.live.at > LIVE_EVERY) pollLive(); });
})();
