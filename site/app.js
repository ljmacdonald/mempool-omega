/* Mempool Omega web app: runs entirely in your browser.
   Live prices: Binance public market data. Models, track record and practice account: this project's
   public repository (updated hourly / nightly by GitHub Actions). */
(function () {
  "use strict";
  const E = window.OmegaEngine;
  const BINANCE = "https://data-api.binance.vision/api/v3";
  const REPO = window.OMEGA_REPO || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/main/state/";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
  };

  const state = {
    style: store.get("omega.style", "short"), refresh: store.get("omega.refresh", 300),
    cfg: null, models: {}, bigMovers: {}, universe: null, universeAt: 0, lastScan: {}, scanning: false,
    timer: null, nextAt: 0, trades: store.get("omega.trades", []), lastAction: {},
  };

  // ------------------------------------------------------------------ formatting
  const price = (x) => (!Number.isFinite(x) ? "–" : x >= 1000 ? `$${x.toLocaleString("en-US", { maximumFractionDigits: 2 })}` :
    x >= 1 ? `$${x.toFixed(x >= 100 ? 2 : 4)}` : `$${x.toPrecision(4)}`);
  const pct = (x, d = 1) => (Number.isFinite(x) ? `${x >= 0 ? "+" : ""}${(x * 100).toFixed(d)}%` : "–");
  const hhmm = (ms) => new Date(ms).toISOString().slice(11, 16);
  const local = (ms) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const gradeClass = (g) => (g === "Strong" ? "good" : g === "Moderate" ? "calm" : g === "Weak" ? "warn" : "bad");
  const riskClass = (r) => (r === "Low" ? "good" : r === "Medium" ? "calm" : r === "High" ? "warn" : "bad");

  // ------------------------------------------------------------------ data access
  async function getJSON(url, timeout = 15000) {
    const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), timeout);
    try { const r = await fetch(url, { signal: ctl.signal, cache: "no-store" }); if (!r.ok) throw new Error(`HTTP ${r.status}`); return await r.json(); }
    finally { clearTimeout(t); }
  }
  async function getText(url) { const r = await fetch(url, { cache: "no-store" }); if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.text(); }

  async function loadConfig() {
    if (!state.cfg) state.cfg = await getJSON(REPO + "web/config.json");
    if (!Object.keys(state.bigMovers).length) { try { state.bigMovers = await getJSON(REPO + "reports/scanner_bigmovers.json"); } catch { /* optional */ } }
  }
  async function loadModel(style) {
    if (!state.models[style]) state.models[style] = await getJSON(REPO + `web/model_${style}.json`, 30000);
    return state.models[style];
  }
  async function loadUniverse() {
    if (state.universe && Date.now() - state.universeAt < 10 * 60000) return state.universe;
    const tickers = await getJSON(`${BINANCE}/ticker/24hr`);
    state.universe = E.selectUniverse(tickers, state.cfg.universe); state.universeAt = Date.now();
    return state.universe;
  }
  async function candles(symbol, interval, n) {
    const rows = await getJSON(`${BINANCE}/klines?symbol=${symbol}&interval=${interval}&limit=${n + 1}`);
    const step = { "5m": 5, "15m": 15, "1h": 60 }[interval] * 60000;
    const closed = rows.filter((r) => r[0] + step <= Date.now());
    const c = { t: [], open: [], high: [], low: [], close: [], volume: [], qv: [], taker_buy_volume: [] };
    for (const r of closed.slice(-n)) {
      c.t.push(r[0]); c.open.push(+r[1]); c.high.push(+r[2]); c.low.push(+r[3]); c.close.push(+r[4]);
      c.volume.push(+r[5]); c.qv.push(+r[7]); c.taker_buy_volume.push(+r[9]);
    }
    return c;
  }
  async function pool(items, size, fn) {
    const out = new Array(items.length); let i = 0;
    await Promise.all(Array.from({ length: size }, async () => {
      while (i < items.length) { const k = i++; try { out[k] = await fn(items[k]); } catch { out[k] = null; } }
    }));
    return out;
  }

  // ------------------------------------------------------------------ scanning
  async function scan(styleKey) {
    await loadConfig();
    const style = state.cfg.styles[styleKey];
    const [model, uni] = await Promise.all([loadModel(styleKey), loadUniverse()]);
    const syms = uni.map((u) => u.symbol);
    if (!syms.includes("BTCUSDT")) syms.push("BTCUSDT");
    const data = await pool(syms, 8, (s) => candles(s, style.interval, style.live_bars));
    const bySym = Object.fromEntries(syms.map((s, i) => [s, data[i]]));
    const coins = uni.map((u) => ({ symbol: u.symbol, quoteVolume: u.quoteVolume, candles: bySym[u.symbol] })).filter((c) => c.candles);
    if (coins.length < 10) throw new Error("not enough market data came back");
    const res = E.rankCoins(coins, bySym.BTCUSDT, model, style, state.cfg, state.bigMovers, 5);
    res.at = Date.now(); res.style = styleKey;
    state.lastScan[styleKey] = res;
    return res;
  }

  async function refreshIdeas(manual) {
    if (state.scanning) return;
    state.scanning = true; $("refreshNow").disabled = true;
    setStatus("Fetching live prices and re-ranking about 60 coins…");
    try {
      const res = await scan(state.style);
      renderIdeas(res);
      setStatus(`Updated ${local(res.at)} · ${res.mood.coins} coins checked`);
    } catch (e) {
      setStatus("Couldn't reach live prices. Showing the latest hourly ideas instead.");
      await renderHourlyFallback(e);
    } finally {
      state.scanning = false; $("refreshNow").disabled = false; schedule(manual);
    }
  }

  function schedule() {
    clearTimeout(state.timer);
    if (state.refresh > 0) { state.nextAt = Date.now() + state.refresh * 1000; state.timer = setTimeout(() => refreshIdeas(false), state.refresh * 1000); }
    else state.nextAt = 0;
  }
  function setStatus(text) { $("status").dataset.base = text; paintStatus(); }
  function paintStatus() {
    const base = $("status").dataset.base || "";
    let extra = "";
    if (state.nextAt && !state.scanning) { const s = Math.max(0, Math.round((state.nextAt - Date.now()) / 1000)); extra = ` · next in ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; }
    $("status").textContent = base + extra;
    $("clock").textContent = `UTC ${new Date().toISOString().slice(11, 16)}`;
  }

  // ------------------------------------------------------------------ rendering: ideas
  function ladder(sl, entry, tp, now) {
    const lo = Math.min(sl, now ?? sl), hi = Math.max(tp, now ?? tp), span = hi - lo || 1;
    const x = (v) => ((v - lo) / span) * 100;
    return `<div class="ladder" aria-hidden="true">
      <div class="bar loss" style="left:${x(sl)}%;width:${x(entry) - x(sl)}%"></div>
      <div class="bar gain" style="left:${x(entry)}%;width:${x(tp) - x(entry)}%"></div>
      <div class="tick" style="left:${x(entry)}%"></div>
      ${now !== undefined ? `<div class="now" style="left:${x(now)}%"></div>` : ""}
    </div>
    <div class="ladder-labels"><span>Safety exit ${price(sl)}</span><span>Buy ${price(entry)}</span><span>Take profit ${price(tp)}</span></div>`;
  }

  function ideaCard(d, hourly) {
    const held = state.trades.some((t) => t.symbol === d.symbol && t.style === d.style);
    const bm = Number.isFinite(d.week_up20_pct) ? `<p class="small muted">Last 90 days: it rose 20%+ within a week ${Math.round(d.week_up20_pct * 100)}% of the time and fell 20%+ ${Math.round(d.week_down20_pct * 100)}% of the time.</p>` : "";
    return `<article class="card">
      <div class="idea-head">
        <h2><span class="rank">${d.rank}.</span>${esc(d.coin)}</h2>
        <div class="score"><b>${d.score.toFixed(1)}</b><span class="muted">/ 10</span>
          <span class="pill ${gradeClass(d.grade)}">${esc(d.grade)}</span>
          <span class="pill ${riskClass(d.risk_level)}">Risk: ${esc(d.risk_level)}</span></div>
      </div>
      <div class="facts">
        <div class="fact"><div class="k">Buy near</div><div class="v">${price(d.price_now)}</div></div>
        <div class="fact"><div class="k">Take profit at</div><div class="v">${price(d.take_profit)}</div><div class="d up">${pct(d.take_profit_pct)}</div></div>
        <div class="fact"><div class="k">Safety exit at</div><div class="v">${price(d.safety_exit)}</div><div class="d down">${pct(d.safety_exit_pct)}</div></div>
        <div class="fact"><div class="k">Sell by</div><div class="v">${local(d.exit_by)}</div><div class="d muted">${hhmm(d.exit_by)} UTC</div></div>
        <div class="fact"><div class="k">Beats the market</div><div class="v">${Math.round(d.chance_beats_market * 100)}%</div><div class="d muted">chance</div></div>
        <div class="fact"><div class="k">To risk $10, buy</div><div class="v">$${Math.round(d.size_for_10usd_risk).toLocaleString()}</div></div>
      </div>
      ${ladder(d.safety_exit, d.price_now, d.take_profit)}
      <div><h3>Why it was picked</h3><ul class="why">${d.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>
      ${d.warnings.length ? `<div class="warnings">${d.warnings.map((w) => `<div class="banner warn">⚠ ${esc(w)}</div>`).join("")}</div>` : ""}
      ${bm}
      <div class="actions">
        ${hourly ? `<span class="small muted">Hourly snapshot. Live prices are unavailable right now.</span>` :
        held ? `<span class="pill calm">You're watching this trade</span>` :
        `<button class="btn primary" type="button" data-take="${esc(d.symbol)}">I bought this: watch it for me</button>`}
      </div>
    </article>`;
  }

  function renderIdeas(res) {
    const m = res.mood, style = state.cfg.styles[res.style];
    const cls = m.label === "Favourable" ? "good" : m.label === "Mixed" ? "calm" : "warn";
    $("mood").className = `banner ${cls}`;
    $("mood").innerHTML = `<b>Market mood: ${m.label}.</b> ${Math.round(m.share_positive * 100)}% of ${m.coins} coins look positive for the ${esc(style.label.split(":")[0])} speed (sell within ${esc(style.hold_text)}). ${m.label === "Unfavourable" ? "Doing nothing is a perfectly good choice right now." : "Scores above 5 are better than break-even after fees."}`;
    $("ideas").innerHTML = `<div style="display:grid;gap:14px">${res.ideas.map((d) => ideaCard(d, false)).join("")}</div>`;
  }

  async function renderHourlyFallback(err) {
    try {
      const pl = await getJSON(REPO + `suggestions/latest_${state.style}.json`);
      const ideas = pl.ideas.map((d) => ({ ...d, exit_by: Date.parse(d.exit_by), chance_beats_market: d.chance_beats_market ?? d.chance_of_profit ?? 0.5 }));
      $("mood").className = "banner warn";
      $("mood").innerHTML = `<b>Live prices are unavailable</b> (${esc(err.message || err)}). Showing the ideas saved at ${esc(pl.generated_at.slice(11, 16))} UTC instead. Prices may have moved since. Binance may be blocked on your network; try another network or try again later.`;
      $("ideas").innerHTML = `<div style="display:grid;gap:14px">${ideas.map((d) => ideaCard(d, true)).join("")}</div>`;
    } catch {
      $("mood").className = "banner bad";
      $("mood").textContent = "Couldn't load ideas right now. Check your internet connection; the page will try again on the next refresh.";
      $("ideas").innerHTML = "";
    }
  }

  // ------------------------------------------------------------------ trades
  function saveTrades() { store.set("omega.trades", state.trades); renderTradeCount(); }
  function renderTradeCount() { const n = state.trades.length; $("tradeCount").hidden = n === 0; $("tradeCount").textContent = n; }

  async function refreshTrades() {
    renderTradeCount();
    const box = $("trades");
    if (!state.trades.length) { box.innerHTML = `<div class="banner calm">No trades yet. Tap “I bought this” on an idea, or add one below.</div>`; updateBackup(); return; }
    let prices = {};
    try { const all = await getJSON(`${BINANCE}/ticker/price`); const want = new Set(state.trades.map((t) => t.symbol)); for (const p of all) if (want.has(p.symbol)) prices[p.symbol] = +p.price; }
    catch { box.insertAdjacentHTML("afterbegin", `<div class="banner warn">Couldn't fetch live prices just now. Retrying in 30 seconds.</div>`); return; }
    const html = [];
    for (const t of state.trades) {
      const px = prices[t.symbol];
      const score = state.lastScan[t.style]?.scores?.[t.symbol];
      const a = E.advise(t, px, Date.now(), score);
      notifyIfChanged(t, a);
      html.push(`<article class="card trade ${a.level}">
        <div class="idea-head"><h2>${esc(t.symbol.replace(/USDT$/, ""))}</h2><span class="verdict ${a.level}">${esc(a.action)}</span></div>
        <p>${esc(a.why)}</p>
        <div class="facts">
          <div class="fact"><div class="k">Bought at</div><div class="v">${price(t.entry)}</div><div class="d muted">${local(t.opened)}</div></div>
          <div class="fact"><div class="k">Price now</div><div class="v">${price(px)}</div><div class="d ${a.pnl >= 0 ? "up" : "down"}">${pct(a.pnl, 2)} after fees</div></div>
          <div class="fact"><div class="k">Take profit at</div><div class="v">${price(t.take_profit)}</div><div class="d muted">${pct(a.toTp)} away</div></div>
          <div class="fact"><div class="k">Safety exit at</div><div class="v">${price(t.safety_exit)}</div><div class="d muted">${pct(a.toSl)} away</div></div>
          <div class="fact"><div class="k">Sell by</div><div class="v">${local(t.exit_by)}</div><div class="d muted">${a.leftMin > 0 ? E.humanDuration(a.leftMin) + " left" : "time is up"}</div></div>
        </div>
        ${ladder(t.safety_exit, t.entry, t.take_profit, px)}
        <div class="actions"><button class="btn" type="button" data-sold="${esc(t.id)}">I've sold it: remove</button></div>
      </article>`);
    }
    box.innerHTML = `<div style="display:grid;gap:14px">${html.join("")}</div><p class="small muted">Prices checked ${local(Date.now())}.</p>`;
    updateBackup();
  }

  function notifyIfChanged(t, a) {
    const prev = state.lastAction[t.id]; state.lastAction[t.id] = a.action;
    if (!prev || prev === a.action || a.action === "Hold") return;
    document.title = `${a.action}: ${t.symbol.replace(/USDT$/, "")} · Mempool Omega`;
    try { if ("Notification" in window && Notification.permission === "granted") new Notification(`${t.symbol.replace(/USDT$/, "")}: ${a.action}`, { body: a.why }); } catch { /* not supported */ }
  }

  function updateBackup() { try { $("backupOut").value = btoa(unescape(encodeURIComponent(JSON.stringify(state.trades)))); } catch { $("backupOut").value = ""; } }

  // ------------------------------------------------------------------ track record & account
  function parseCSV(text) {
    const lines = text.trim().split(/\r?\n/); const head = lines.shift().split(",");
    return lines.map((l) => { const v = l.split(","); return Object.fromEntries(head.map((h, i) => [h, v[i]])); });
  }

  async function renderRecord() {
    const box = $("record");
    try {
      await loadConfig();
      const b = await getJSON(REPO + "suggestions/scoreboard.json");
      const rows = Object.entries(b.by_style || {}).filter(([, v]) => v.closed);
      let html = "";
      if (!rows.length) html += `<div class="banner calm">No ideas have reached their time limit yet. Check back in a few hours.</div>`;
      else {
        html += `<div class="table-wrap"><table><thead><tr><th>Speed</th><th class="num">Ideas checked</th><th class="num">Ended in profit</th><th class="num">Average per idea</th><th class="num">Random pick average</th><th class="num">$100 in each idea</th></tr></thead><tbody>`;
        for (const [k, v] of rows) {
          const beat = v.avg_return_per_idea > v.random_pick_avg_return;
          html += `<tr><td>${esc(state.cfg.styles[k]?.label || k)}</td><td class="num">${v.closed}</td><td class="num">${Math.round(v.win_rate * 100)}%</td>
            <td class="num ${v.avg_return_per_idea >= 0 ? "up" : "down"}">${pct(v.avg_return_per_idea, 2)}</td>
            <td class="num">${pct(v.random_pick_avg_return, 2)} ${beat ? '<span class="pill good">beating random</span>' : '<span class="pill warn">not beating random</span>'}</td>
            <td class="num ${v.if_100usd_each_total_pnl >= 0 ? "up" : "down"}">${v.if_100usd_each_total_pnl >= 0 ? "+" : "−"}$${Math.abs(v.if_100usd_each_total_pnl).toFixed(0)}</td></tr>`;
        }
        html += `</tbody></table></div><p class="small muted">If a speed doesn't beat “random pick” over a few weeks, its ranking isn't adding value.</p>`;
      }
      try {
        const h = parseCSV(await getText(REPO + "suggestions/history.csv")).filter((r) => r.status === "closed").slice(-25).reverse();
        if (h.length) {
          html += `<h3>Latest checked ideas</h3><div class="table-wrap"><table><thead><tr><th>Idea made (UTC)</th><th>Speed</th><th>Coin</th><th class="num">Score</th><th>What happened</th><th class="num">Result</th></tr></thead><tbody>`;
          const what = { take_profit: "Hit take profit", safety_exit: "Hit safety exit", time_limit: "Time limit reached" };
          for (const r of h) html += `<tr><td>${esc(r.ts.slice(5, 16))}</td><td>${esc(r.style || "day")}</td><td>${esc(r.symbol.replace(/USDT$/, ""))}</td><td class="num">${esc(r.score)}</td><td>${esc(what[r.outcome] || r.outcome)}</td><td class="num ${+r.net_ret >= 0 ? "up" : "down"}">${pct(+r.net_ret, 2)}</td></tr>`;
          html += `</tbody></table></div>`;
        }
      } catch { /* history optional */ }
      box.innerHTML = html;
    } catch { box.innerHTML = `<div class="banner warn">Couldn't load the track record right now.</div>`; }
  }

  function sparkline(points) {
    if (points.length < 2) return `<p class="small muted">The balance chart appears after a few hourly runs.</p>`;
    const W = 640, H = 160, P = 28; const ys = points.map((p) => p[1]); let lo = Math.min(...ys), hi = Math.max(...ys);
    if (hi - lo < 1) { lo -= 50; hi += 50; }
    const x = (i) => P + (i / (points.length - 1)) * (W - 2 * P), y = (v) => H - P - ((v - lo) / (hi - lo)) * (H - 2 * P);
    const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
    const last = points[points.length - 1];
    return `<div class="table-wrap"><svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Practice balance over time">
      <line x1="${P}" x2="${W - P}" y1="${y(100000)}" y2="${y(100000)}" stroke="var(--line)" stroke-dasharray="4 4"/>
      <path d="${d}" fill="none" stroke="var(--accent)" stroke-width="2"/>
      <circle cx="${x(points.length - 1)}" cy="${y(last[1])}" r="4" fill="var(--accent)"/>
      <text x="${P}" y="14">$${Math.round(hi).toLocaleString()}</text><text x="${P}" y="${H - 6}">$${Math.round(lo).toLocaleString()}</text>
      <text x="${W - P}" y="${H - 6}" text-anchor="end">${esc(new Date(last[0]).toISOString().slice(5, 16).replace("T", " "))} UTC</text>
    </svg></div>`;
  }

  async function renderAccount() {
    const box = $("account");
    try {
      const s = await getJSON(REPO + "status.json");
      const ks = s.kill_switch || {};
      const month = new Date().toISOString().slice(0, 7);
      let pts = [];
      try { pts = parseCSV(await getText(REPO + `ledger/equity_${month}.csv`)).map((r) => [Date.parse(r.ts), +r.equity]).filter((p) => Number.isFinite(p[1])); } catch { /* new month */ }
      const pos = Object.values(s.positions || {});
      box.innerHTML = `<div class="stats">
          <div class="stat"><div class="k">Practice balance</div><div class="v num">$${Math.round(s.equity).toLocaleString()}</div><div class="small ${s.equity >= 100000 ? "up" : "down"}">${pct(s.equity / 100000 - 1, 2)} since start</div></div>
          <div class="stat"><div class="k">Open practice trades</div><div class="v">${pos.length}</div></div>
          <div class="stat"><div class="k">Data trust</div><div class="v">${(s.systemic_trust ?? 0).toFixed(2)}</div><div class="small muted">0–1: how real today's signals look</div></div>
          <div class="stat"><div class="k">Emergency stop</div><div class="v">${ks.active ? "On" : "Ready"}</div><div class="small muted">${ks.active ? esc(ks.reason) : "Closes everything if losses get too big"}</div></div>
        </div>
        ${sparkline(pts)}
        <p class="small muted">Last check ${esc((s.run_at || "").slice(0, 16).replace("T", " "))} UTC. The practice account only trades Bitcoin and Ether, and only when it expects a gain larger than fees plus a safety margin, so days without trades are normal.</p>`;
    } catch { box.innerHTML = `<div class="banner warn">Couldn't load the practice account right now.</div>`; }
  }

  // ------------------------------------------------------------------ wiring
  function selectTab(name) {
    for (const b of document.querySelectorAll("nav.tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === name));
    for (const p of document.querySelectorAll("section.panel")) p.hidden = p.id !== `tab-${name}`;
    if (name === "record") renderRecord();
    if (name === "account") renderAccount();
    if (name === "trades") refreshTrades();
    store.set("omega.tab", name);
    if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  }

  function selectStyle(key) {
    state.style = key; store.set("omega.style", key);
    for (const b of document.querySelectorAll("#speed button")) b.setAttribute("aria-pressed", String(b.dataset.style === key));
    $("ideas").innerHTML = `<div class="skeleton"></div>`;
    refreshIdeas(true);
  }

  document.addEventListener("click", (ev) => {
    const take = ev.target.closest("[data-take]");
    if (take) {
      const idea = state.lastScan[state.style]?.ideas.find((d) => d.symbol === take.dataset.take);
      if (idea) {
        state.trades.push(E.makeTrade(idea)); saveTrades();
        take.outerHTML = `<span class="pill good">Added. Open “My trades” to see when to sell.</span>`;
      }
    }
    const sold = ev.target.closest("[data-sold]");
    if (sold) { state.trades = state.trades.filter((t) => t.id !== sold.dataset.sold); saveTrades(); refreshTrades(); }
  });
  for (const b of document.querySelectorAll("#speed button")) b.addEventListener("click", () => selectStyle(b.dataset.style));
  for (const b of document.querySelectorAll("nav.tabs button")) b.addEventListener("click", () => selectTab(b.dataset.tab));
  $("refresh").addEventListener("change", (e) => { state.refresh = +e.target.value; store.set("omega.refresh", state.refresh); schedule(); paintStatus(); });
  $("refreshNow").addEventListener("click", () => { state.universe = null; refreshIdeas(true); });

  $("notifyBtn").addEventListener("click", async () => {
    if (!("Notification" in window)) { $("notifyState").textContent = "This browser doesn't support alerts. The tab title changes instead."; return; }
    const p = await Notification.requestPermission();
    $("notifyState").textContent = p === "granted" ? "Alerts are on while this page is open." : "Alerts are off. The tab title will still change when it's time to act.";
  });

  $("manualForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    let coin = $("mCoin").value.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (!coin) return;
    if (!coin.endsWith("USDT")) coin += "USDT";
    const entry = +$("mPrice").value, ago = +$("mAgo").value || 0, risk = +$("mRisk").value / 100;
    const style = $("mSpeed").value; const minutes = { quick: 60, short: 240, day: 1440 }[style];
    try { await getJSON(`${BINANCE}/ticker/price?symbol=${coin}`); }
    catch { $("manualMsg").textContent = `Couldn't find ${coin} on Binance. Check the coin name (for example SOL, ETH, DOGE).`; return; }
    const opened = Date.now() - ago * 60000;
    state.trades.push(E.makeTrade({ symbol: coin, style, price_now: entry, risk_unit: risk, hold_minutes: minutes }, entry, opened));
    saveTrades(); $("manualMsg").textContent = `Watching ${coin.replace(/USDT$/, "")}.`; $("manualForm").reset(); refreshTrades();
  });
  $("mCoin").addEventListener("change", async () => {
    let coin = $("mCoin").value.trim().toUpperCase(); if (!coin) return; if (!coin.endsWith("USDT")) coin += "USDT";
    try { const p = await getJSON(`${BINANCE}/ticker/price?symbol=${coin}`); if (!$("mPrice").value) $("mPrice").value = (+p.price).toPrecision(6); } catch { /* ignore */ }
  });

  $("copyBackup").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("backupOut").value); $("backupMsg").textContent = "Copied."; }
    catch { $("backupOut").select(); $("backupMsg").textContent = "Select the code and copy it."; }
  });
  $("loadBackup").addEventListener("click", () => {
    try {
      const add = JSON.parse(decodeURIComponent(escape(atob($("backupIn").value.trim()))));
      if (!Array.isArray(add)) throw new Error("bad");
      const ids = new Set(state.trades.map((t) => t.id));
      for (const t of add) if (t && t.symbol && !ids.has(t.id)) state.trades.push(t);
      saveTrades(); refreshTrades(); $("backupMsg").textContent = `Loaded ${add.length} trade(s).`;
    } catch { $("backupMsg").textContent = "That code didn't work. Copy it again from the other device."; }
  });

  // ------------------------------------------------------------------ start
  $("refresh").value = String(state.refresh);
  for (const b of document.querySelectorAll("#speed button")) b.setAttribute("aria-pressed", String(b.dataset.style === state.style));
  const startTab = (location.hash || "").slice(1) || store.get("omega.tab", "ideas");
  selectTab(["ideas", "trades", "record", "account", "guide"].includes(startTab) ? startTab : "ideas");
  renderTradeCount();
  refreshIdeas(true);
  setInterval(paintStatus, 1000);
  setInterval(() => { if (state.trades.length) refreshTrades(); }, 30000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && state.nextAt && Date.now() > state.nextAt) refreshIdeas(false); });
})();
