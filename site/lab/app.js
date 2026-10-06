/* Strategy lab: famous public strategies re-tested by lab/run.py (nightly) with where each one stands now (every
   15 minutes). Reads the snapshot published on the data branch. Prices shown are always labelled with the time
   they're from; when the snapshot is old, positions are hidden instead of shown as if current. */
(() => {
  "use strict";
  const DATA = window.OMEGA_LAB_DATA || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/data/lab/";
  const STALE_MIN = 45, LIVE_AGE = 60000, STOP_OUT = 0.5;
  const BINANCE = "https://data-api.binance.vision/api/v3";
  const Q = window.OmegaQuality;
  const store = { get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } } };
  const state = { snap: null, stale: true, money: store.get("omega.lab.money", 100), lev: store.get("omega.lab.lev", 1), live: {} };
  const LEVEL = { good: [0, "Held up", "good"], warn: [1, "Not proven", "warn"], bad: [2, "Failed", "bad"] };
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fin = (x) => typeof x === "number" && Number.isFinite(x);
  const pct = (x, d = 2) => (fin(x) ? `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(d)}%` : "–");
  const pc0 = (x) => (fin(x) ? `${Math.round(x * 100)}%` : "–");
  const num = (x) => (fin(x) ? (Math.abs(x) >= 1000 ? x.toLocaleString(undefined, { maximumFractionDigits: 2 }) : Math.abs(x) >= 1 ? x.toFixed(2) : x.toPrecision(4)) : "–");
  const day = (t) => new Date(t).toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" });
  const when = (t) => new Date(t).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const hold = (h) => (!fin(h) ? "–" : h < 2 ? `${Math.round(h * 60)} min` : h < 48 ? `${h.toFixed(1)} h` : `${(h / 24).toFixed(1)} days`);
  const cls = (x) => (fin(x) ? (x > 0 ? "up" : "down") : "");

  const usd = (x) => (fin(x) ? `${x >= 0 ? "+" : "−"}$${Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "–");
  const livePx = (sym) => { const l = state.live[sym]; return l && Date.now() - l.at < LIVE_AGE ? l.px : null; };

  // ------------------------------------------------------------------ trade ideas
  function calcHTML(x) {
    const amt = state.money, lev = state.lev, pos = amt * lev; const c = x.cost || 0;
    const rows = [];
    if (fin(x.avg_win)) rows.push(["If it goes like this strategy's average win here", pos * x.avg_win]);
    if (fin(x.avg_loss)) rows.push(["If it goes like its average loss", pos * x.avg_loss]);
    if (x.stop) rows.push(["If the safety exit is hit", pos * (-(x.sl_pct || 0) - c)]);
    if (x.target) rows.push(["If the take profit is hit", pos * (x.side * (x.target / x.entry - 1) - c)]);
    const out = x.sl_pct || Math.abs(x.avg_loss || 0); const stopOut = STOP_OUT / lev;
    return `<div class="calc"><h3>Profit calculator: $${amt.toLocaleString()} of your money, ${lev === 1 ? "no leverage" : `${lev}x leverage`} (a $${pos.toLocaleString()} position)</h3>
      <table><tbody>${rows.map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num ${cls(v)}">${usd(v)}</td></tr>`).join("")}</tbody></table>
      ${lev > 1 ? `<div class="banner ${out >= stopOut ? "bad" : "warn"}">At ${lev}x, a move of about ${(stopOut * 100).toFixed(1)}% against you loses half your money and most platforms close the trade.${out >= stopOut ? " That's within this strategy's usual losses: lower the leverage." : ""}</div>` : ""}
      <p class="small muted">After costs (about ${(c * 100).toFixed(2)}% of the position). Averages are from the re-test on this market; one trade can do much better or much worse.</p></div>`;
  }
  function ideaCard(x, q, stale) {
    const px = livePx(x.sym); const ref = px || x.last; const since = x.side * (ref / x.entry - 1);
    const lateNow = px && fin(x.avg_win) && x.avg_win > 0 && since > 0.5 * x.avg_win && !x.late;
    const warns = [...(x.warnings || []), ...(lateNow ? [`Since the update the live price has moved ${pct(since)} in the trade's favour: much of the usual gain may be gone.`] : [])];
    const gw = Q.gradeWord(x.grade, q, x.rank); const gc = Q.gradeClassFor(x.grade, q);
    return `<article class="card idea" data-alert-key="idea-${esc(x.id)}"><div class="idea-head"><h3><span class="rank">#${x.rank}</span>${x.side > 0 ? "BUY" : "SELL"} ${esc(x.label)} <span class="muted small">· ${esc(x.name)}</span></h3>
      <div class="score"><b>${x.score.toFixed(1)}</b><span class="muted">/ 10</span> <span class="pill ${gc}">${esc(gw)}</span> <span class="pill ${x.level === "good" ? "good" : x.level === "bad" ? "bad" : "warn"}">${esc(x.evidence)}</span></div></div>
      <div class="facts">
        <div class="fact"><div class="k">Strategy entered</div><div class="v">${num(x.entry)}</div><div class="d muted">${when(x.t_in)}</div></div>
        <div class="fact"><div class="k">${px ? "Price now (live)" : "Price at last update"}</div><div class="v">${stale && !px ? "–" : num(ref)}</div><div class="d ${px ? cls(since) : "muted"}">${px ? `${pct(since)} since the entry` : stale ? "out of date" : `at ${when(x.asof)}${x.sym.endsWith("USDT") ? " · waiting for the live price" : " · no free live price: check your broker"}`}</div></div>
        <div class="fact"><div class="k">Chance it makes money</div><div class="v">${pc0(x.prob)}</div><div class="d muted">from ${x.hist_n} past trades here</div></div>
        <div class="fact"><div class="k">Expected, after costs</div><div class="v ${cls(x.exp_r_adj)}">${x.exp_r_adj >= 0 ? "+" : "−"}${Math.abs(x.exp_r_adj).toFixed(2)}R</div><div class="d muted">per $1 put at risk, after warnings</div></div>
        <div class="fact"><div class="k">Safety exit</div><div class="v">${x.stop ? num(x.stop) : "none"}</div><div class="d muted">${x.stop ? pct(-(x.sl_pct || 0)) + " from entry" : `average loss ${pct(x.avg_loss)}`}</div></div>
        <div class="fact"><div class="k">Usually held</div><div class="v">${hold(x.hold_h)}</div><div class="d muted">until the strategy's own exit</div></div>
      </div>
      ${warns.length ? `<ul class="warnings">${warns.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
      <p class="small"><b>How it exits:</b> ${esc(x.exit_rule)}</p>
      ${calcHTML(x)}</article>`;
  }
  function ideas(S) {
    const q = S.quality; const list = S.ideas || [];
    $("ideasBanner").innerHTML = Q.ideasBanner(q);
    if (state.stale) { $("ideas").innerHTML = `<div class="banner warn">The latest update is more than ${STALE_MIN} minutes old, so ideas are hidden rather than shown with old prices. They come back with the next update.</div>`; }
    else if (!list.length) { $("ideas").innerHTML = `<div class="banner calm"><b>No fresh entries right now.</b> These strategies only trade when their exact rules fire: a few times a day for the fast crypto ones, a few times a year for the daily ones. Checked every 15 minutes.</div>`; }
    else {
      const good = list.filter((x) => x.level === "good").length;
      $("ideas").innerHTML = (good ? "" : `<div class="banner warn"><b>None of today's ideas come from a strategy that held up in the re-test.</b> They're listed so you can follow along; treat them as watch-only.</div>`) + list.map((x) => ideaCard(x, q, state.stale)).join("");
    }
    const f = (S.forming || []).filter((x) => x.above200);
    $("forming").innerHTML = !f.length ? "" : `<h3>Forming at today's close: Connors RSI(2)</h3><div class="table-wrap"><table><thead><tr><th>Market</th><th class="num">Price</th><th class="num">2-day RSI if it closed now</th><th>Buy at the close?</th></tr></thead><tbody>
      ${f.map((x) => `<tr data-alert-key="forming-${esc(x.sym)}-${new Date(x.asof).toISOString().slice(0, 10)}"><td>${esc(x.label)}</td><td class="num">${num(x.price)} <span class="muted small">at ${when(x.asof)}</span></td><td class="num">${fin(x.rsi2) ? x.rsi2.toFixed(0) : "–"}</td><td>${x.signal ? '<span class="pill good">Likely, if it stays here</span>' : "Not now (needs under 10)"}</td></tr>`).join("")}
      </tbody></table></div><p class="small muted">The only strategy here that held up buys at the close, so the decision is made in the last minutes before 4 pm New York. This uses the latest 5-minute price as a stand-in for the close.</p>`;
    const ex = S.exits || [];
    $("exits").innerHTML = !ex.length ? "" : `<h3>Exit signals</h3><div class="table-wrap"><table><thead><tr><th>Strategy</th><th>Market</th><th>Entered</th><th>Closed</th><th class="num">Entry → exit price</th><th class="num">Result after costs</th><th>Why</th></tr></thead><tbody>
      ${ex.map((e) => `<tr data-alert-key="exit-${esc(e.strategy)}-${esc(e.sym)}-${e.t_in}"><td class="small">${esc(e.name)}</td><td>${e.side > 0 ? "Buy" : "Sell"} ${esc(e.label)}</td><td class="small">${when(e.t_in)}</td><td class="small">${when(e.t_out)}</td><td class="num small">${num(e.entry)} → ${num(e.exit)}</td><td class="num ${cls(e.net)}">${pct(e.net)}</td><td class="small">${esc(e.reason)}</td></tr>`).join("")}
      </tbody></table></div><p class="small muted">If you followed one of these strategies' trades, this is where it got out.</p>`;
  }
  function record(S) {
    const r = S.record || {}; const pr = S.probation || {};
    const rows = Object.entries(S.strategies).map(([k, v]) => { const x = r[k] || {}; const p = pr[k] || {};
      return `<tr><td>${esc(v.name)}${p.active ? ' <span class="pill warn">probation</span>' : ""}</td><td class="num">${x.n ?? 0}</td><td class="num">${x.open ?? 0}</td><td class="num">${pc0(x.hit)}</td><td class="num ${cls(x.avg)}">${pct(x.avg)}</td><td class="num">${pct(x.random)}</td></tr>`; }).join("");
    $("recordBody").innerHTML = `<p class="small muted">Every idea is recorded when it first appears and followed to the strategy's own exit, with the grade it was given, so the grades are checked against what really happened. Started ${day(Date.parse(S.added))}.</p>
      <div class="table-wrap"><table><thead><tr><th>Strategy</th><th class="num">Finished</th><th class="num">Still open</th><th class="num">Made money</th><th class="num">Average after costs</th><th class="num">Random entries</th></tr></thead><tbody>${rows}</tbody></table></div>
      ${Q.gradeTable(S.quality, "How each grade actually turned out (all strategies)")}
      ${(r.recent || []).length ? `<details><summary>Latest finished ideas</summary><div class="table-wrap"><table><tbody>${r.recent.map((x) => `<tr><td class="small">${when(x.t_in)}</td><td>${x.side > 0 ? "Buy" : "Sell"} ${esc(x.label)}</td><td class="small">${esc(x.name)}</td><td>${esc(x.grade)}</td><td class="num ${cls(x.net)}">${pct(x.net)}</td></tr>`).join("")}</tbody></table></div></details>` : ""}
      <p class="small muted">A strategy goes on probation (its scores lowered) if, over 30 or more finished ideas, they do worse than random entries.</p>`;
  }
  async function pollLive() {
    const syms = [...new Set((state.snap?.ideas || []).map((x) => x.sym).filter((x) => x.endsWith("USDT")))];
    if (!syms.length) return;
    try {
      const r = await fetch(`${BINANCE}/ticker/price?symbols=${encodeURIComponent(JSON.stringify(syms))}`, { cache: "no-store" });
      if (!r.ok) return;
      for (const t of await r.json()) state.live[t.symbol] = { px: +t.price, at: Date.now() };
      if (state.snap) ideas(state.snap);
    } catch { /* stays "waiting for the live price" */ }
  }

  function row(name, a) {
    return `<tr><td>${name}</td><td class="num">${a.n ?? 0}</td><td class="num">${pc0(a.hit)}</td><td class="num ${cls(a.avg)}">${pct(a.avg)}</td><td class="num">${pct(a.random)}</td><td class="num ${cls(a.edge)}">${pct(a.edge)}</td></tr>`;
  }
  const HEAD = `<thead><tr><th></th><th class="num">Trades</th><th class="num">Hit rate</th><th class="num">Average per trade after costs</th><th class="num">Random entries</th><th class="num">Better than random by</th></tr></thead>`;

  function board(S) {
    const list = Object.entries(S.strategies).filter(([, v]) => v.stats).sort((a, b) => LEVEL[a[1].stats.level][0] - LEVEL[b[1].stats.level][0]);
    $("board").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Strategy</th><th>Style</th><th class="num">Trades</th><th class="num">Hit rate</th><th class="num">Average per trade</th><th class="num">Random entries</th><th>Verdict</th></tr></thead><tbody>
      ${list.map(([k, v]) => { const a = v.stats.all; const L = LEVEL[v.stats.level];
        return `<tr><td><a href="#s-${k}"><b>${esc(v.name)}</b></a></td><td class="small">${esc(v.style)}</td><td class="num">${a.n}</td><td class="num">${pc0(a.hit)}</td><td class="num ${cls(a.avg)}">${pct(a.avg)}</td><td class="num">${pct(a.random)}</td><td><span class="pill ${L[2]}">${L[1]}</span></td></tr>`; }).join("")}
      </tbody></table></div>`;
    const good = list.filter(([, v]) => v.stats.level === "good").map(([, v]) => v.name);
    const hiHit = list.filter(([, v]) => v.stats.all.hit >= 0.8 && !(v.stats.all.avg > 0)).map(([, v]) => `${v.name} (${pc0(v.stats.all.hit)} hit rate)`);
    $("summary").className = `banner ${good.length ? "good" : "warn"}`;
    $("summary").innerHTML = `<b>${list.length} famous public strategies re-tested. ${good.length ? `${good.length} held up: ${esc(good.join(", "))}.` : "None held up."}</b> ` +
      `${list.length - good.length} did not: they lost money after costs, did no better than random entries, or stopped working once they were published.` +
      (hiHit.length ? ` Note the trap: ${esc(hiHit.join(", "))} won most of its trades and still lost money overall.` : "") +
      ` <span class="small">Tested ${S.stats_at ? `on ${day(Date.parse(S.stats_at))}` : "nightly"}.</span>`;
  }

  function nowBlock(k, v, stale) {
    const open = v.open || [];
    let html = "";
    if (v.watch && Object.keys(v.watch).length) {
      html += `<div class="table-wrap"><table><thead><tr><th>Market</th><th class="num">2-day RSI</th><th>Above 200-day average?</th><th>Buy signal at the close?</th></tr></thead><tbody>
        ${Object.entries(v.watch).map(([sym, w]) => { const m = v.markets.find((x) => x.sym === sym) || { label: sym }; const sig = w.above200 && w.rsi2 < 10;
          return `<tr><td>${esc(m.label)}</td><td class="num">${fin(w.rsi2) ? w.rsi2.toFixed(0) : "–"}</td><td>${w.above200 ? "Yes" : "No: no buying"}</td><td>${sig ? '<span class="pill good">Yes</span>' : `No${w.above200 ? " (needs RSI under 10)" : ""}`}</td></tr>`; }).join("")}
        </tbody></table></div><p class="small muted">From the last finished trading day (${v.asof && Object.values(v.asof)[0] ? `closed ${when(Object.values(v.asof)[0])}` : ""}). The rule buys at the close, so in practice the order goes in during the last minutes of the session.</p>`;
    }
    if (!open.length) return html + `<p class="muted">Not in any trade right now.</p>`;
    if (stale) return html + `<div class="banner warn">The latest update is more than ${STALE_MIN} minutes old, so open positions are hidden rather than shown with old prices.</div>`;
    return html + `<div class="table-wrap"><table><thead><tr><th>Market</th><th>Side</th><th>Entered</th><th class="num">Entry</th><th class="num">Price then</th><th class="num">So far</th><th class="num">Safety exit</th>${open.some((o) => o.target) ? '<th class="num">Take profit</th>' : ""}</tr></thead><tbody>
      ${open.map((o) => { const so = o.side * (o.last / o.entry - 1);
        return `<tr><td>${esc(o.label)}</td><td>${o.side > 0 ? "Buy" : "Sell"}</td><td class="small">${when(o.t_in)}</td><td class="num">${num(o.entry)}</td><td class="num">${num(o.last)} <span class="muted small">at ${when(o.asof)}</span></td><td class="num ${cls(so)}">${pct(so)}</td><td class="num">${o.stop ? num(o.stop) : "none"}</td>${open.some((x) => x.target) ? `<td class="num">${o.target ? num(o.target) : "–"}</td>` : ""}</tr>`; }).join("")}
      </tbody></table></div><p class="small muted">The strategy's own paper positions, not a recommendation${v.stats && v.stats.level !== "good" ? ": this strategy did <b>not</b> hold up in the tests" : ""}. "Price then" is the last finished candle's close, not a live price.</p>`;
  }

  function card(k, v, stale) {
    const s = v.stats; const L = LEVEL[s.level]; const a = s.all;
    const later = s.split === "publication" ? `After it was published (${day(Date.parse(v.published))} on)` : "Most recent third";
    const earlier = s.split === "publication" ? "Before it was published" : "First two thirds";
    const markets = Object.values(s.markets || {});
    return `<article class="card" id="s-${k}">
      <h2>${esc(v.name)} <span class="pill ${L[2]}">${L[1]}</span></h2>
      <p class="small muted">${esc(v.style)} · ${esc(v.tf === "1d" ? "daily candles" : v.tf === "1h" ? "1-hour candles" : "5-minute candles")} · source: <a href="${esc(v.url)}" target="_blank" rel="noopener">${esc(v.source)}</a></p>
      <p><b>The rules:</b> ${esc(v.rules)}</p>
      <p><b>What's claimed:</b> ${esc(v.claim)}</p>
      <div class="banner ${L[2]}"><b>Our test:</b> ${esc(s.verdict)}</div>
      <div class="stats">
        <div class="stat"><div class="k">Trades tested</div><div class="v">${a.n}</div></div>
        <div class="stat"><div class="k">Hit rate</div><div class="v">${pc0(a.hit)}</div></div>
        <div class="stat"><div class="k">Average per trade</div><div class="v ${cls(a.avg)}">${pct(a.avg)}</div></div>
        <div class="stat"><div class="k">Random entries</div><div class="v">${pct(a.random)}</div></div>
        <div class="stat"><div class="k">Average win / loss</div><div class="v">${pct(a.avg_win, 1)} / ${pct(a.avg_loss, 1)}</div></div>
        <div class="stat"><div class="k">Average time in a trade</div><div class="v">${hold(a.hold_h)}</div></div>
        ${fin(a.avg_r) ? `<div class="stat"><div class="k">Per $1 risked</div><div class="v ${cls(a.avg_r)}">${a.avg_r >= 0 ? "+" : "−"}${Math.abs(a.avg_r).toFixed(2)}</div></div>` : ""}
      </div>
      <p class="small muted">Tested ${s.from ? day(s.from) : "?"} to ${s.to ? day(s.to) : "?"}. Costs per round trip: ${esc(v.markets.map((m) => `${m.label} ${(m.cost * 100).toFixed(2)}%`).join(", "))}.</p>
      <div class="table-wrap"><table>${HEAD}<tbody>${row(earlier, s.early)}${row(`<b>${later}</b>`, s.late)}${s.since_added && s.since_added.n ? row("Since it was added to this site", s.since_added) : ""}</tbody></table></div>
      <details><summary>Each market</summary><div class="table-wrap"><table>${HEAD}<tbody>${markets.map((m) => row(esc(m.label), m)).join("")}</tbody></table></div></details>
      <h3>Right now</h3>${nowBlock(k, v, stale)}
      ${(s.recent || []).length ? `<details><summary>Latest finished trades</summary><div class="table-wrap"><table><thead><tr><th>Entered</th><th>Closed</th><th>Market</th><th>Side</th><th class="num">Entry → exit price</th><th class="num">Result after costs</th><th>Why it closed</th></tr></thead><tbody>
        ${s.recent.map((r) => `<tr><td class="small">${when(r.t_in)}</td><td class="small">${when(r.t_out)}</td><td>${esc(r.label)}</td><td>${r.side > 0 ? "Buy" : "Sell"}</td><td class="num small">${num(r.entry)} → ${num(r.exit)}</td><td class="num ${cls(r.net)}">${pct(r.net)}</td><td class="small">${esc(r.reason)}</td></tr>`).join("")}
        </tbody></table></div></details>` : ""}
    </article>`;
  }

  async function load() {
    try {
      const r = await fetch(`${DATA}snapshot.json?t=${Date.now()}`, { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      const S = await r.json();
      const age = (Date.now() - Date.parse(S.generated_at)) / 60000; const stale = !(age <= STALE_MIN);
      $("status").textContent = `Updated ${Math.round(age)} min ago`;
      $("status").className = `status${stale ? " stale" : ""}`;
      state.snap = S; state.stale = stale;
      board(S); ideas(S); record(S); pollLive();
      const list = Object.entries(S.strategies).filter(([, v]) => v.stats).sort((a, b) => LEVEL[a[1].stats.level][0] - LEVEL[b[1].stats.level][0]);
      $("cards").innerHTML = list.map(([k, v]) => card(k, v, stale)).join("");
    } catch (e) {
      $("summary").className = "banner warn";
      $("summary").textContent = "Couldn't load the test results. They are produced by the project's GitHub Actions; try again in a few minutes.";
    }
  }
  $("money").value = String(state.money); $("lev").value = String(state.lev);
  $("money").addEventListener("change", (e) => { state.money = Math.max(1, +e.target.value || 100); store.set("omega.lab.money", state.money); if (state.snap) ideas(state.snap); });
  $("lev").addEventListener("change", (e) => { state.lev = +e.target.value || 1; store.set("omega.lab.lev", state.lev); if (state.snap) ideas(state.snap); });
  load();
  setInterval(load, 5 * 60 * 1000);
  setInterval(pollLive, 15000);
})();
