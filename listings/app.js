/* New listings: what listed recently on Binance, OKX and Gate.io, what's about to list, graded ideas from the
   listing rules that held up (listings/run.py, every 30 minutes) and the test of every rule on every listing since
   2023 (listings/research.py, nightly). Prices are labelled with their time; Binance coins get a live price. When the
   snapshot is old, ideas are hidden instead of shown with old prices. */
(() => {
  "use strict";
  const DATA = window.OMEGA_LISTINGS_DATA || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/data/listings/";
  const STALE_MIN = 75, LIVE_AGE = 60000, STOP_OUT = 0.5;
  const BINANCE = "https://data-api.binance.vision/api/v3";
  const EX = { binance: "Binance", okx: "OKX", gate: "Gate.io" };
  const Q = window.OmegaQuality;
  const $ = (id) => document.getElementById(id);
  const store = { get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } } };
  const state = { snap: null, stale: true, money: store.get("omega.lst.money", 100), lev: store.get("omega.lst.lev", 1), live: {} };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fin = (x) => typeof x === "number" && Number.isFinite(x);
  const pct = (x, d = 1) => (fin(x) ? `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(d)}%` : "–");
  const pc0 = (x) => (fin(x) ? `${Math.round(x * 100)}%` : "–");
  const cls = (x) => (fin(x) ? (x > 0 ? "up" : "down") : "");
  const num = (x) => (fin(x) ? (Math.abs(x) >= 1000 ? x.toLocaleString(undefined, { maximumFractionDigits: 2 }) : Math.abs(x) >= 1 ? x.toFixed(3) : x.toPrecision(4)) : "–");
  const usd = (x) => (fin(x) ? `${x >= 0 ? "+" : "−"}$${Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "–");
  const money = (x) => (fin(x) ? (x >= 1e6 ? `$${(x / 1e6).toFixed(1)}M` : `$${Math.round(x / 1e3)}k`) : "–");
  const when = (t) => new Date(t).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const day = (t) => new Date(t).toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" });
  const age = (d) => (d < 1 ? `${Math.max(1, Math.round(d * 24))} h` : `${Math.round(d)} days`);
  const livePx = (x) => { const l = x.exchange === "binance" ? state.live[x.sym] : null; return l && Date.now() - l.at < LIVE_AGE ? l.px : null; };

  // ------------------------------------------------------------------ summary
  function summary(S) {
    const f = (S.research || {}).facts || {}; const rules = (S.research || {}).rules || {};
    const good = Object.values(rules).filter((r) => r.level === "good").map((r) => r.name);
    $("summary").className = "banner warn";
    $("summary").innerHTML = `<b>What happened after ${f.listings || "the"} crypto listings on ${esc((S.research.exchanges || []).map((e) => EX[e]).join(" and "))} since ${esc(S.research.since)}:</b>
      on the first day the price typically spiked to ${fin(f.median_day0_high) ? `${(1 + f.median_day0_high).toFixed(1)}×` : "–"} its opening price, then the typical coin was
      <b>${pct(f.median_30d, 0)} after 30 days</b> and ${pct(f.median_90d, 0)} after 90. ${pc0(f.share_down_30d)} were lower a month later and ${pc0(f.share_halved_90d)} lost half their value within 90 days.
      <b>Buying new listings lost money in every way we tested.</b> ${good.length ? `What held up: ${esc(good.join(", "))}, i.e. selling early, not buying.` : ""}`;
  }

  // ------------------------------------------------------------------ ideas
  function calcHTML(x) {
    const pos = state.money * state.lev; const c = x.cost || 0;
    const rows = [];
    if (fin(x.avg_win)) rows.push(["If it goes like this rule's average win", pos * x.avg_win]);
    if (fin(x.avg_loss)) rows.push(["If it goes like its average loss", pos * x.avg_loss]);
    rows.push(["If the safety exit is hit (30% against you)", pos * (-0.3 - c)]);
    const stopOut = STOP_OUT / state.lev;
    return `<div class="calc"><h3>Profit calculator: $${state.money.toLocaleString()} of your money, ${state.lev === 1 ? "no leverage" : `${state.lev}x leverage`} (a $${pos.toLocaleString()} position)</h3>
      <table><tbody>${rows.map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num ${cls(v)}">${usd(v)}</td></tr>`).join("")}</tbody></table>
      ${state.lev > 1 ? `<div class="banner bad">At ${state.lev}x, a ${(stopOut * 100).toFixed(0)}% move against you loses half your money, and new listings often move that much in a day. Leverage on new listings is how most people lose everything.</div>` : ""}
      <p class="small muted">After fees and slippage (about ${(c * 100).toFixed(1)}% round trip)${x.side < 0 ? " and the assumed short funding fee" : ""}. Averages are from the test; one trade can do much better or much worse.</p></div>`;
  }
  function ideaCard(x, q) {
    const px = livePx(x); const ref = px || x.last; const since = x.side * (ref / x.entry - 1);
    const gw = Q.gradeWord(x.grade, q, x.rank); const gc = Q.gradeClassFor(x.grade, q);
    const verb = x.side > 0 ? "BUY" : "SELL";
    return `<article class="card idea" data-alert-key="idea-${esc(x.id)}"><div class="idea-head"><h3><span class="rank">#${x.rank}</span>${verb} ${esc(x.base)} <span class="muted small">on ${esc(EX[x.exchange])} · ${esc(x.name)}</span></h3>
      <div class="score"><b>${x.score.toFixed(1)}</b><span class="muted">/ 10</span> <span class="pill ${gc}">${esc(gw)}</span> <span class="pill ${x.level === "good" ? "good" : "warn"}">${esc(x.evidence)}</span></div></div>
      <div class="facts">
        <div class="fact"><div class="k">Rule's entry</div><div class="v">${num(x.entry)}</div><div class="d muted">${when(x.t_in)} · listed ${when(x.listed)}</div></div>
        <div class="fact"><div class="k">${px ? "Price now (live)" : "Price at last update"}</div><div class="v">${state.stale && !px ? "–" : num(ref)}</div><div class="d ${px ? cls(since) : "muted"}">${px ? `${pct(since)} for this idea since the entry` : state.stale ? "out of date" : `at ${when(x.asof)}`}</div></div>
        <div class="fact"><div class="k">Chance it works</div><div class="v">${pc0(x.prob)}</div><div class="d muted">from ${x.hist_n} past listings</div></div>
        <div class="fact"><div class="k">Expected, after costs</div><div class="v ${cls(x.exp_r_adj)}">${x.exp_r_adj >= 0 ? "+" : "−"}${Math.abs(x.exp_r_adj).toFixed(2)}R</div><div class="d muted">per $1 put at risk, after warnings</div></div>
        <div class="fact"><div class="k">Safety exit</div><div class="v">${num(x.stop)}</div><div class="d muted">30% ${x.side > 0 ? "below" : "above"} the entry</div></div>
        <div class="fact"><div class="k">Close it after</div><div class="v">${x.days} days</div><div class="d muted">${when(x.t_in + x.days * 86400000)}</div></div>
      </div>
      <p><b>The rule:</b> ${esc(x.rule_text)}</p>
      ${x.side < 0 && fin(x.holder_avg) ? `<p class="small"><b>If you already hold it</b> (airdrop, launchpool): selling at this point and not buying back for ${x.days} days averaged ${pct(x.holder_avg)} better than holding, with no futures account or funding fee needed.</p>` : ""}
      ${x.warnings.length ? `<ul class="warnings">${x.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
      ${calcHTML(x)}</article>`;
  }
  function ideas(S) {
    const list = S.ideas || []; const q = S.quality;
    $("ideasBanner").innerHTML = Q.ideasBanner(q);
    if (state.stale) $("ideas").innerHTML = `<div class="banner warn">The latest update is more than ${STALE_MIN} minutes old, so ideas are hidden rather than shown with old prices.</div>`;
    else if (!list.length) $("ideas").innerHTML = `<div class="banner calm"><b>No fresh listing ideas right now.</b> They only appear in the first hours and days after a listing, when a rule that held up fires. Checked every 30 minutes; turn on the alerts (bell at the top) to hear about them.</div>`;
    else $("ideas").innerHTML = list.map((x) => ideaCard(x, q)).join("");
    const c = S.cautions || [];
    $("cautions").innerHTML = !c.length ? "" : `<h3>Don't be tempted</h3><div class="table-wrap"><table><thead><tr><th>Coin</th><th>Rule firing now</th><th class="num">How it did in the test</th></tr></thead><tbody>
      ${c.map((x) => `<tr><td><b>${esc(x.base)}</b> <span class="muted small">${esc(EX[x.exchange])}</span></td><td>${esc(x.name)}</td><td class="num down">${pct(x.avg)} average over ${x.days} days, ${pc0(x.hit)} made money (${x.n} listings)</td></tr>`).join("")}
      </tbody></table></div><p class="small muted">These rules fired on a new listing just now, but they lost money after costs in the test, so they're not ideas.</p>`;
  }

  // ------------------------------------------------------------------ tables
  function recent(S) {
    const rows = S.listings || [];
    $("recent").innerHTML = !rows.length ? `<p class="muted">No new crypto listings in the last 45 days on these exchanges.</p>` : `<div class="table-wrap"><table><thead><tr><th>Coin</th><th>Exchange</th><th>Listed</th><th class="num">Since first price</th><th class="num">From its high</th><th class="num">Traded (last day)</th><th>Stage</th></tr></thead><tbody>
      ${rows.map((r) => `<tr data-alert-key="listing-${esc(r.exchange)}-${esc(r.symbol)}"><td><b>${esc(r.base)}</b></td><td>${esc(EX[r.exchange])}</td><td class="small">${day(r.list_ms)} · ${age(r.age_d)} ago</td><td class="num ${cls(r.chg)}">${pct(r.chg, 0)}</td><td class="num ${cls(r.from_high)}">${pct(r.from_high, 0)}</td><td class="num">${money(r.vol24)}</td><td class="small">${esc(r.phase)}</td></tr>`).join("")}
      </tbody></table></div><p class="small muted">Prices at ${S.generated_at ? when(Date.parse(S.generated_at)) : "the last update"}. Tokenized stocks and stablecoins are left out. Gate.io lists many small coins: only those trading $500k+ a day are shown, and Gate listings weren't part of the test.</p>`;
  }
  function upcoming(S) {
    const u = S.upcoming || [];
    $("upcoming").innerHTML = !u.length ? `<p class="muted">Nothing scheduled on OKX or Gate.io in the next 14 days. (Binance doesn't publish listing times in its market data; its announcements come first.)</p>`
      : `<div class="table-wrap"><table><thead><tr><th>Coin</th><th>Exchange</th><th>Trading opens</th></tr></thead><tbody>${u.map((x) => `<tr data-alert-key="upcoming-${esc(x.exchange)}-${esc(x.symbol)}"><td><b>${esc(x.base)}</b></td><td>${esc(EX[x.exchange])}</td><td>${when(x.list_ms)}</td></tr>`).join("")}</tbody></table></div>
      <p class="small muted">The first hours are the wildest. In the test, buying in the first hour lost money on average; selling after it held up best.</p>`;
  }
  function research(S) {
    const R = S.research || {}; const rules = R.rules || {};
    const order = Object.entries(rules).sort((a, b) => ({ good: 0, warn: 1, bad: 2 }[a[1].level] - { good: 0, warn: 1, bad: 2 }[b[1].level]));
    const L = { good: ["Held up", "good"], warn: ["Not proven", "warn"], bad: ["Failed", "bad"] };
    $("research").innerHTML = `<p class="small muted">Every crypto listing on ${esc((R.exchanges || []).map((e) => EX[e]).join(" and "))} since ${esc(R.since)}, delisted coins included. Each rule is held 7 or 30 days (chosen on the older listings) with a 30% safety exit, after ${((R.cost || 0) * 100).toFixed(1)}% fees and slippage${R.funding ? ` (shorts also pay an assumed ${(R.funding * 100).toFixed(1)}% a day funding)` : ""}, and compared with holding big coins (BTC, ETH, SOL, XRP, BNB, DOGE, ADA, LINK) over the same days. "Newer listings" is the most recent third, judged separately.</p>
      <div class="table-wrap"><table><thead><tr><th>Rule</th><th class="num">Held</th><th class="num">Trades</th><th class="num">Made money</th><th class="num">Average after costs</th><th class="num">Holding big coins</th><th class="num">Newer listings</th><th>Verdict</th></tr></thead><tbody>
      ${order.map(([, r]) => { const h = r.holds[String(r.chosen)]; const a = h.all, l = h.late;
        return `<tr><td><b>${esc(r.name)}</b><div class="small muted" style="white-space:normal;max-width:30em">${esc(r.text)}</div></td><td class="num">${r.chosen} days</td><td class="num">${a.n}</td><td class="num">${pc0(a.hit)}</td><td class="num ${cls(a.avg)}">${pct(a.avg)}</td><td class="num">${pct(a.random)}</td><td class="num ${cls(l.avg)}">${pct(l.avg)} <span class="muted small">(${l.n})</span></td><td><span class="pill ${L[r.level][1]}">${L[r.level][0]}</span></td></tr>`; }).join("")}
      </tbody></table></div>
      ${order.filter(([, r]) => r.holds[String(r.chosen)].holder).map(([, r]) => `<p class="small"><b>${esc(r.name)}, for people who already hold the coin:</b> selling instead of holding was ${pct(r.holds[String(r.chosen)].holder.avg)} better on average over ${r.chosen} days (no futures, no funding fee).</p>`).join("")}
      <p class="small muted">Tested ${R.generated_at ? day(Date.parse(R.generated_at)) : "nightly"}. "Made money" alone means little: what matters is the average after costs and whether it beat simply holding big coins.</p>`;
  }
  function record(S) {
    const r = S.record || {}; const pr = S.probation || {}; const names = S.names || {};
    $("recordBody").innerHTML = `<p class="small muted">Every idea is recorded when it first appears and followed to its exit, with the grade it was given.</p>
      <div class="table-wrap"><table><thead><tr><th>Rule</th><th class="num">Finished</th><th class="num">Still open</th><th class="num">Made money</th><th class="num">Average after costs</th><th class="num">Holding big coins</th></tr></thead><tbody>
      ${Object.entries(names).map(([k, n]) => { const x = r[k] || {}; return `<tr><td>${esc(n)}${(pr[k] || {}).active ? ' <span class="pill warn">probation</span>' : ""}</td><td class="num">${x.n ?? 0}</td><td class="num">${x.open ?? 0}</td><td class="num">${pc0(x.hit)}</td><td class="num ${cls(x.avg)}">${pct(x.avg)}</td><td class="num">${pct(x.random)}</td></tr>`; }).join("")}
      </tbody></table></div>${Q.gradeTable(S.quality, "How each grade actually turned out")}`;
  }

  // ------------------------------------------------------------------ live prices (Binance coins) and loading
  async function pollLive() {
    const syms = [...new Set((state.snap?.ideas || []).filter((x) => x.exchange === "binance").map((x) => x.sym))];
    if (!syms.length) return;
    try {
      const r = await fetch(`${BINANCE}/ticker/price?symbols=${encodeURIComponent(JSON.stringify(syms))}`, { cache: "no-store" });
      if (!r.ok) return;
      for (const t of await r.json()) state.live[t.symbol] = { px: +t.price, at: Date.now() };
      if (state.snap) ideas(state.snap);
    } catch { /* stays "price at last update" */ }
  }
  async function load() {
    try {
      const r = await fetch(`${DATA}snapshot.json?t=${Date.now()}`, { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      const S = await r.json();
      const ageMin = (Date.now() - Date.parse(S.generated_at)) / 60000;
      state.snap = S; state.stale = !(ageMin <= STALE_MIN);
      $("status").textContent = `Updated ${Math.round(ageMin)} min ago`; $("status").className = `status${state.stale ? " stale" : ""}`;
      summary(S); ideas(S); recent(S); upcoming(S); research(S); record(S); pollLive();
    } catch {
      $("summary").className = "banner warn";
      $("summary").textContent = "Couldn't load the listings data. It's produced by the project's GitHub Actions; try again in a few minutes.";
    }
  }
  $("money").value = String(state.money); $("lev").value = String(state.lev);
  $("money").addEventListener("change", (e) => { state.money = Math.max(1, +e.target.value || 100); store.set("omega.lst.money", state.money); if (state.snap) ideas(state.snap); });
  $("lev").addEventListener("change", (e) => { state.lev = +e.target.value || 1; store.set("omega.lst.lev", state.lev); if (state.snap) ideas(state.snap); });
  load();
  setInterval(load, 5 * 60 * 1000);
  setInterval(pollLive, 15000);
})();
