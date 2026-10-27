/* Perp DEX lab (DECISIONS D123): renders perp/run.py's hourly paper snapshot and daily history tests (data branch
   perp/snapshot.json and perp/research.json). Shows what the files say; nothing is recomputed or invented here. */
(() => {
  "use strict";
  const PAGE = document.body.dataset.page;
  const DATA = window.OMEGA_PERP_DATA || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/data/perp/";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (x) => (x === null || x === undefined || x === "" ? null : Number(x));
  const fin = (x) => Number.isFinite(num(x));
  const pct = (x, dp = 1) => (fin(x) ? `${num(x) >= 0 ? "+" : "−"}${Math.abs(num(x) * 100).toFixed(dp)}%` : "–");
  const share = (x, dp = 0) => (fin(x) ? `${(num(x) * 100).toFixed(dp)}%` : "–");
  const usd = (x, dp = 2) => (fin(x) ? `${num(x) < 0 ? "−" : ""}$${Math.abs(num(x)).toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp })}` : "–");
  const ago = (t) => { const m = Math.round((Date.now() - Date.parse(t)) / 60000); return !Number.isFinite(m) ? "" : m < 60 ? `${m} min ago` : m < 2880 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`; };
  const tone = (x) => (fin(x) ? (num(x) > 0 ? "up" : num(x) < 0 ? "down" : "") : "");
  const NAMES = { lp: "1 · Pool liquidity (be the house)", lp_hedged: "1h · Pool liquidity, hedged", carry: "2 · Funding carry (hedged)",
    spread: "3 · Cross-venue spread", trend: "4a · Directional trend", reversion: "4b · Mean reversion",
    trend_long_only: "Benchmark: long-only momentum", trend_short_only: "Benchmark: short-only momentum" };
  const PAGES = { lp: ["lp", "lp_hedged"], carry: ["carry"], spread: ["spread"], directional: ["trend", "reversion"] };
  const LIVE = { lp: ["lp"], carry: ["carry"], spread: ["spread"], directional: ["trend", "reversion"] };
  const GATES = [["data", "Data integrity"], ["evidence", "Enough evidence"], ["profit", "Net profit (95% lower bound > 0)"],
    ["drawdown", "Drawdown ≤ 10%"], ["robust", "Still profitable with costs ×2"], ["shadow", "30 days of live paper"]];
  const STATUS = { pass: ["Pass", "good"], fail: ["Fail", "bad"], incomplete: ["Not yet", "calm"] };
  let R = null, S = null;

  const pill = (st) => { const [t, c] = STATUS[st] || [st, "calm"]; return `<span class="pill ${c}">${esc(t)}</span>`; };
  const statusPill = (st) => `<span class="pill ${st === "failed" ? "bad" : st === "ineligible" ? "warn" : st && st.startsWith("passed") ? "good" : "calm"}">${esc(st === "failed" ? "Failed" : st === "ineligible" ? "Can't trade at this size" : st && st.startsWith("passed") ? "Passed (review only)" : "Testing")}</span>`;

  function spark(curve) {
    const ys = (curve || []).map((p) => num(p[1])).filter(Number.isFinite);
    if (ys.length < 2) return '<p class="small muted">The curve appears after a few hourly runs.</p>';
    const lo = Math.min(...ys), hi = Math.max(...ys), w = 600, h = 120;
    const pts = ys.map((y, i) => `${(i / (ys.length - 1) * w).toFixed(1)},${(h - (hi === lo ? h / 2 : (y - lo) / (hi - lo) * h)).toFixed(1)}`).join(" ");
    return `<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:120px" role="img" aria-label="Equity curve"><polyline fill="none" stroke="var(--accent)" stroke-width="2" points="${pts}"/></svg>
      <p class="small muted">${usd(lo)} to ${usd(hi)}</p>`;
  }

  function waterfall(steps) {
    if (!steps || !steps.length) return '<p class="small muted">No closed trades yet.</p>';
    return `<div class="table-wrap"><table><tbody>${steps.map((s, i) => `<tr${i === 0 || i === steps.length - 1 ? ' class="sel"' : ""}><td>${esc(s.step)}</td><td class="num ${tone(s.amount)}">${usd(s.amount)}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function gatesTable(keys) {
    const rows = keys.map((k) => {
      const s = (R.strategies || {})[k] || {};
      const g = ((s.account || {}).gates || {}).gates || {};
      const sh = (((S || {}).shadow || {}).account || {})[k === "lp_hedged" ? "lp" : k] || {};
      return `<tr><td><b>${esc(NAMES[k] || k)}</b></td>${GATES.map(([gk]) => {
        let st = (g[gk] || {}).status;
        if (gk === "shadow") st = (sh.days || 0) >= 30 && (sh.cost_ok ?? 0) >= 0.95 ? "pass" : "incomplete";
        return `<td>${pill(st)}</td>`;
      }).join("")}<td>${statusPill(((s.account || {}).gates || {}).status)}</td></tr>`;
    }).join("");
    return `<div class="table-wrap"><table><thead><tr><th>Strategy</th>${GATES.map(([, l]) => `<th>${esc(l)}</th>`).join("")}<th>Overall</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="small muted">Gates are judged on the $100 account. Profit, robustness and data are judged only once there is enough evidence (500 trades, or 500 days for the pool strategy); until then they show "Not yet". A drawdown beyond 10% fails at any sample size. Passing every gate would only allow a review: there is no live trading in any case.</p>`;
  }

  function compare(keys) {
    const row = (k) => {
      const s = (R.strategies || {})[k] || {};
      const a = s.account || {}, r = s.reference || {};
      const gross = num(r.gross), cost = num(r.costs);
      return `<tr><td><b>${esc(NAMES[k] || k)}</b></td><td class="num ${tone(a.total_return)}">${pct(a.total_return)}</td><td class="num">${a.n ?? a.trades ?? 0}</td>
        <td class="num ${tone(r.total_return)}">${pct(r.total_return)}</td><td class="num">${pct(r.annual_return)}</td><td class="num">${share(r.max_drawdown)}</td>
        <td class="num">${fin(gross) && gross > 0 && fin(cost) ? share(cost / gross) : "–"}</td><td class="num">${r.n ?? r.trades ?? 0}</td><td>${statusPill(((a.gates || {}).status))}</td></tr>`;
    };
    const bench = (((R.strategies || {}).lp || {}).benchmark_hold) || {};
    return `<div class="table-wrap"><table><thead><tr><th>Strategy</th><th class="num">$100 account</th><th class="num">Trades</th><th class="num">$10,000 account</th><th class="num">A year</th><th class="num">Worst drop</th><th class="num">Costs / gross</th><th class="num">Trades</th><th>Gates</th></tr></thead>
      <tbody>${keys.map(row).join("")}
      <tr><td>Benchmark: do nothing (cash)</td><td class="num">+0.0%</td><td class="num">0</td><td class="num">+0.0%</td><td class="num">+0.0%</td><td class="num">0%</td><td class="num">–</td><td class="num">0</td><td></td></tr>
      ${bench.days ? `<tr><td>Benchmark: hold the ETH and BTC pools (no rotation)</td><td class="num">–</td><td class="num">–</td><td class="num ${tone(bench.total_return)}">${pct(bench.total_return)}</td><td class="num">${pct(bench.annual_return)}</td><td class="num">${share(bench.max_drawdown)}</td><td class="num">–</td><td class="num">1</td><td></td></tr>` : ""}
      </tbody></table></div>
      <p class="small muted">History tests with every cost; the $10,000 account runs the same rules where fixed costs (about ${usd(R.execution_fee_usd)} per GMX order) matter less, to tell an edge apart from small-account friction. Each strategy gets an equal share of the account (directional's share is split between 4a and 4b).</p>`;
  }

  function historyCard(k) {
    const s = (R.strategies || {})[k] || {};
    const blk = (book, label) => {
      const x = s[book] || {};
      const per = Object.entries(x.by_year || x.by_period || {}).slice(-24);
      return `<h4>${label}</h4><div class="stats">
        <div class="stat"><div class="k">Result after costs</div><div class="v ${tone(x.total_return)}">${pct(x.total_return)}</div><div class="small muted">${x.n ?? x.trades ?? 0} ${k.startsWith("lp") ? "pool holdings" : "trades"}</div></div>
        <div class="stat"><div class="k">Worst drop</div><div class="v">${share(x.max_drawdown)}</div></div>
        <div class="stat"><div class="k">Costs doubled</div><div class="v ${tone(x.stressed_mean)}">${fin(x.stressed_mean) ? pct(x.stressed_mean, 3) : "–"}</div><div class="small muted">average per ${k.startsWith("lp") ? "day" : "trade"}</div></div>
        <div class="stat"><div class="k">With no costs</div><div class="v ${tone((x.cost_blind || {}).total_return)}">${pct((x.cost_blind || {}).total_return)}</div><div class="small muted">diagnostic only</div></div></div>
        ${per.length ? `<details><summary>By ${x.by_year ? "year" : "month"}</summary><div class="table-wrap"><table><tbody>${per.map(([p, v]) => `<tr><td>${esc(p)}</td><td class="num ${tone(typeof v === "object" ? v.total : v)}">${typeof v === "object" ? `${pct(v.total)} (${v.n} trades)` : pct(v)}</td></tr>`).join("")}</tbody></table></div></details>` : ""}
        ${Object.keys(x.by_market || {}).length ? `<details><summary>By market</summary><div class="table-wrap"><table><tbody>${Object.entries(x.by_market).sort((a, b) => b[1].total - a[1].total).slice(0, 30).map(([m, v]) => `<tr><td>${esc(m)}</td><td class="num">${v.n}</td><td class="num ${tone(v.total)}">${pct(v.total)}</td></tr>`).join("")}</tbody></table></div></details>` : ""}
        ${x.ineligible ? `<p class="small"><b>${esc(x.ineligible)}</b></p>` : ""}
        ${x.rejected ? `<p class="small muted">${x.rejected} signals not taken: ${Object.entries(x.rejected_why || {}).map(([w, n]) => `${esc(w)} (${n})`).join("; ")}.</p>` : ""}
        <details><summary>Cost waterfall</summary>${waterfall(x.waterfall)}</details>`;
    };
    return `<article class="card"><h2>${esc(NAMES[k] || k)}: history test</h2>${blk("account", "$100 account")}${blk("reference", "$10,000 reference account")}
      ${k === "spread" && s.gaps ? gapTable(s.gaps) : ""}
      ${s.account && s.account.opportunities ? `<p class="small">Gaps beyond 2.5 standard deviations seen: ${s.account.opportunities.seen}; wide enough to pay twice the costs: ${s.account.opportunities.taken}.</p>` : ""}</article>`;
  }

  function gapTable(g) {
    return `<h4>How often GMX and Hyperliquid prices differ by more than twice the costs ($1,000 position)</h4><div class="table-wrap"><table><thead><tr><th>Market</th><th class="num">Hours</th><th class="num">Typical gap</th><th class="num">95th percentile</th><th class="num">Round-trip cost</th><th class="num">Hours over 2× costs</th><th class="num">Longest</th></tr></thead><tbody>
      ${Object.entries(g).map(([m, x]) => `<tr><td>${esc(m)}</td><td class="num">${x.hours}</td><td class="num">${share(x.median_gap, 3)}</td><td class="num">${share(x.p95_gap, 3)}</td><td class="num">${share(x.cost_share_1000, 2)}</td><td class="num">${x.hours_over_2x_costs}</td><td class="num">${x.longest_hours} h</td></tr>`).join("")}</tbody></table></div>`;
  }

  function liveCard(keys) {
    if (!S) return '<article class="card"><h2>Live paper accounts</h2><p class="muted">The first hourly run hasn\'t published yet.</p></article>';
    const one = (k, book) => {
      const a = ((S.books || {})[book] || {})[k];
      if (!a) return "";
      const open = a.open || [];
      return `<h4>${esc(NAMES[k])} · ${book === "account" ? "$100 account" : "$10,000 reference"}</h4>
        <div class="stats"><div class="stat"><div class="k">Value</div><div class="v ${tone(a.net)}">${usd(a.equity)}</div><div class="small muted">from ${usd(a.start)} · ${pct(num(a.net) / num(a.start))}</div></div>
        <div class="stat"><div class="k">Status</div><div class="v">${a.paused ? "Paused" : "Running"}</div><div class="small muted">${esc(a.paused || "paper mode")}</div></div>
        <div class="stat"><div class="k">Open</div><div class="v">${open.length}</div></div><div class="stat"><div class="k">Closed trades</div><div class="v">${a.n_closed}</div></div></div>
        ${spark(a.curve)}
        ${open.length ? `<div class="table-wrap"><table><thead><tr><th>Market</th><th>Side</th><th class="num">Size</th><th class="num">Entry</th><th>Opened</th><th class="num">Costs so far</th><th>Stop / target</th></tr></thead><tbody>
          ${open.map((p) => `<tr><td>${esc(p.market)}</td><td>${p.side > 0 ? "Long" : p.side < 0 ? "Short" : "Pool"}${p.hedge_entry ? " (hedged)" : ""}</td><td class="num">${usd(p.n)}</td><td class="num">${fin(p.entry) ? num(p.entry).toPrecision(6) : "–"}</td><td class="small">${esc(ago(p.opened))}</td><td class="num">${usd(Object.values(p.costs || {}).reduce((s, v) => s + num(v), 0), 4)}</td><td class="small">${p.stop ? `${num(p.stop).toPrecision(6)} / ${num(p.target).toPrecision(6)}` : "–"}</td></tr>`).join("")}</tbody></table></div>` : ""}
        ${(a.closed || []).length ? `<details><summary>Closed trades (${a.n_closed}) · <a href="#" data-csv="${esc(k)}|${esc(book)}">download CSV</a></summary><div class="table-wrap"><table><thead><tr><th>Market</th><th>Side</th><th>Opened → closed</th><th class="num">Gross</th><th class="num">Costs</th><th class="num">Net</th><th>Why it closed</th></tr></thead><tbody>
          ${a.closed.map((t) => `<tr><td>${esc(t.market)}</td><td>${t.side > 0 ? "Long" : t.side < 0 ? "Short" : "Pool"}</td><td class="small">${esc((t.opened || "").slice(0, 16))} → ${esc((t.closed || "").slice(0, 16))}</td><td class="num">${usd(t.gross, 4)}</td><td class="num">${usd(Object.values(t.costs || {}).reduce((s, v) => s + num(v), 0), 4)}</td><td class="num ${tone(t.net)}">${usd(t.net, 4)}</td><td class="small">${esc(t.reason)}</td></tr>`).join("")}</tbody></table></div>
          <h4>Cost waterfall (live)</h4>${waterfall(a.waterfall)}</details>` : ""}`;
    };
    return `<article class="card" id="live"><h2>Live paper accounts</h2><p class="small muted">Started ${esc((S.started || "").slice(0, 10))}. Every hour each strategy acts on live GMX and Hyperliquid data through the same cost model and risk rules. Prices are GMX's oracle and Hyperliquid's mid; impact, fees and execution fees are charged as costs. Nothing is ever signed or sent. Pause and flatten happen automatically (risk limits and kill switches): a public static page has no private controls.</p>
      ${keys.map((k) => one(k, "account") + one(k, "reference")).join("")}</article>`;
  }

  function marketsCard() {
    if (!S) return "";
    const m = S.markets || {};
    return `<article class="card" id="markets"><h2>Markets now</h2>${(S.kill || []).length ? `<div class="banner bad"><b>Kill switch on:</b> ${esc(S.kill.join("; "))}. No new positions; open ones are flattened.</div>` : ""}
      <div class="table-wrap"><table><thead><tr><th>Market</th><th class="num">GMX oracle</th><th class="num">Hyperliquid</th><th class="num">Gap</th><th class="num">Long / short open interest</th><th class="num">GMX funding (long, a year)</th><th class="num">GMX borrowing (long)</th><th class="num">Hyperliquid funding (a year)</th><th>Data</th></tr></thead><tbody>
      ${Object.entries(m).map(([k, x]) => { const oi = num(x.oi_long) + num(x.oi_short); return `<tr><td><b>${esc(x.name)}</b></td><td class="num">${fin(x.oracle) ? num(x.oracle).toPrecision(6) : "–"}</td><td class="num">${fin(x.hl_mid) ? num(x.hl_mid).toPrecision(6) : "–"}</td>
        <td class="num">${fin(x.oracle) && fin(x.hl_mid) ? share(num(x.oracle) / num(x.hl_mid) - 1, 3) : "–"}</td><td class="num">${oi ? `${share(num(x.oi_long) / oi)} / ${share(num(x.oi_short) / oi)}` : "–"}</td>
        <td class="num">${pct(-x.funding_long_annual)}</td><td class="num">${pct(-x.borrow_long_annual)}</td><td class="num">${pct(x.hl_funding_annual)}</td>
        <td class="small">${x.quality && x.quality.reasons && x.quality.reasons.length ? esc(x.quality.reasons.join("; ")) : '<span class="pill good">OK</span>'}</td></tr>`; }).join("")}</tbody></table></div>
      <p class="small muted">GMX funding and borrowing shown as what a long earns (negative: pays). Execution fee per GMX order now ${usd(S.exec_fee_usd)}. USDC ${fin(S.usdc) ? num(S.usdc).toFixed(4) : "–"}. Updated ${esc(ago(S.generated_at))}.</p></article>`;
  }

  function execgapCard() {
    const x = R.execgap;
    if (!x || !x.orders) return "";
    const rows = Object.entries(x.by_size).map(([b, v]) => { const m = (o) => (o ? o.median.toFixed(2) : "–"); return `<tr><td>${esc(b)}</td><td class="num">${v.orders}</td><td class="num">${m(v.gap)}</td><td class="num">${m(v.documented)}</td><td class="num">${m(v.residual)}</td><td class="num">${m(v.fee)}</td><td class="num">${v.delay ? v.delay.median : "–"} s</td><td class="num">${m(v.move)}</td></tr>`; }).join("");
    return `<article class="card" id="execgap"><h2>Do big orders get worse fills? (real GMX orders)</h2>
      <p>${x.orders.toLocaleString()} executed GMX market orders from the last 7 days, by size (medians, in basis points; positive = worse for the trader). ${esc(x.verdict || "")}</p>
      <div class="table-wrap"><table><thead><tr><th>Order size</th><th class="num">Orders</th><th class="num">Fill vs oracle</th><th class="num">GMX's reported impact</th><th class="num">Unexplained</th><th class="num">Fee</th><th class="num">Wait</th><th class="num">Oracle move while waiting</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="small muted">"Unexplained" is the fill's distance from the oracle price minus the impact GMX itself reports (ANEE: observed − predicted). A size-dependent unexplained cost would deserve a closer look; it would not by itself show intent. Passive observation only: no orders are sent.</p></article>`;
  }

  function decisionsCard(keys) {
    if (!S) return "";
    const ds = (S.decisions || []).filter((r) => !keys || keys.includes(r.strategy)).slice(0, 60);
    const counts = (S.decision_counts || []).filter((r) => !keys || keys.includes(r.strategy)).slice(0, 20);
    return `<article class="card" id="decisions"><h2>Decision log</h2><p class="small muted">This hour's decisions, rejected ones included (the full log is kept day by day in the repository: state/perp/decisions).</p>
      ${counts.length ? `<div class="table-wrap"><table><thead><tr><th>Strategy</th><th>Decision</th><th>Why</th><th class="num">Count</th></tr></thead><tbody>${counts.map((c) => `<tr><td>${esc(NAMES[c.strategy] || c.strategy)}</td><td>${esc(c.decision)}</td><td class="small">${esc(c.why)}</td><td class="num">${c.n}</td></tr>`).join("")}</tbody></table></div>` : ""}
      ${ds.length ? `<details><summary>Latest ${ds.length} decisions</summary><div class="table-wrap"><table><tbody>${ds.map((r) => `<tr><td class="small">${esc((r.t || "").slice(11, 16))}</td><td>${esc(r.book === "account" ? "$100" : "$10k")}</td><td>${esc(NAMES[r.strategy] || r.strategy)}</td><td>${esc(r.market || "")}</td><td>${esc(r.decision)}</td><td class="small">${esc(r.why)}</td></tr>`).join("")}</tbody></table></div></details>` : ""}</article>`;
  }

  function summary() {
    const st = R ? Object.entries(R.strategies || {}).filter(([k]) => !k.includes("_only")) : [];
    const failed = st.filter(([, s]) => ((s.account || {}).gates || {}).status === "failed").length;
    const passed = st.filter(([, s]) => (((s.account || {}).gates || {}).status || "").startsWith("passed")).length;
    $("summary").className = `banner ${passed ? "good" : "calm"}`;
    $("summary").innerHTML = R ? `<b>Paper only.</b> ${st.length} strategy tests: ${passed} passed every gate, ${failed} failed one, ${st.length - passed - failed} still collecting evidence. History tested ${esc(ago(R.generated_at))}${S ? `; live paper updated ${esc(ago(S.generated_at))}` : ""}.`
      : "Waiting for the first history test (runs daily).";
    const s = $("status"); if (s) s.textContent = S ? `Updated ${ago(S.generated_at)}` : "Paper only";
  }

  function render() {
    summary();
    const main = $("main");
    if (PAGE === "overview") {
      const keys = ["lp", "lp_hedged", "carry", "spread", "trend", "reversion", "trend_long_only", "trend_short_only"];
      main.innerHTML = (R ? `<article class="card" id="compare"><h2>Which approach earns after costs?</h2>${compare(keys)}</article>
        <article class="card" id="gates"><h2>Go / no-go gates</h2>${gatesTable(keys.filter((k) => !k.includes("_only")))}</article>` : "")
        + marketsCard() + liveCard(["lp", "carry", "spread", "trend", "reversion"]) + (R ? execgapCard() : "") + decisionsCard(null);
    } else {
      const keys = PAGES[PAGE] || [];
      main.innerHTML = (R ? keys.map(historyCard).join("") + `<article class="card"><h2>Go / no-go gates</h2>${gatesTable(keys)}</article>` : "")
        + liveCard(LIVE[PAGE] || keys) + (PAGE !== "lp" ? marketsCard() : "") + decisionsCard(LIVE[PAGE] || keys);
    }
    for (const a of document.querySelectorAll("a[data-csv]")) a.addEventListener("click", (e) => { e.preventDefault(); csv(...a.dataset.csv.split("|")); });
  }

  function csv(k, book) {
    const a = ((S.books || {})[book] || {})[k];
    const rows = [["strategy", "book", "market", "side", "notional", "opened", "closed", "gross", "costs", "net", "reason"]];
    for (const t of a.closed || []) rows.push([k, book, t.market, t.side, t.notional, t.opened, t.closed, t.gross, Object.values(t.costs || {}).reduce((s, v) => s + num(v), 0), t.net, t.reason]);
    const blob = new Blob([rows.map((r) => r.map((x) => `"${String(x ?? "").replace(/"/g, '""')}"`).join(",")).join("\n")], { type: "text/csv" });
    const u = URL.createObjectURL(blob); const l = document.createElement("a"); l.href = u; l.download = `perp-${k}-${book}.csv`; l.click(); URL.revokeObjectURL(u);
  }

  async function load() {
    const get = async (f) => { try { const r = await fetch(`${DATA}${f}?t=${Date.now()}`, { cache: "no-store" }); return r.ok ? await r.json() : null; } catch { return null; } };
    [R, S] = await Promise.all([get("research.json"), get("snapshot.json")]);
    if (!R && !S) {
      $("summary").className = "banner warn";
      $("summary").innerHTML = "<b>The Perp DEX lab's data isn't available yet.</b> The hourly paper job and the daily history test publish it shortly after the lab goes live.";
      return;
    }
    render();
  }
  load(); setInterval(load, 15 * 60e3);
})();
