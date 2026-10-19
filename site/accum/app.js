/* Accumulation lab (DECISIONS D119): from accum/run.py (data branch accum/snapshot.json, refreshed hourly).
   Shows what the file says, with its sources and limits; nothing is recomputed or invented here. */
(() => {
  "use strict";
  const DATA = window.OMEGA_ACCUM_DATA || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/data/accum/";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fin = (x) => typeof x === "number" && Number.isFinite(x);
  const pct = (x, dp = 1) => (fin(x) ? `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(dp)}%` : "–");
  const share = (x) => (fin(x) ? `${(x * 100).toFixed(x < 0.001 && x > -0.001 ? 3 : 2)}%` : "unknown");
  const usd = (x, dp = 0) => (fin(x) ? `${x < 0 ? "−" : ""}$${Math.abs(x).toLocaleString(undefined, { maximumFractionDigits: dp, minimumFractionDigits: dp })}` : "–");
  const big = (x) => (!fin(x) ? "–" : Math.abs(x) >= 1e9 ? `$${(x / 1e9).toFixed(2)}B` : Math.abs(x) >= 1e6 ? `$${(x / 1e6).toFixed(1)}M` : Math.abs(x) >= 1e3 ? `$${(x / 1e3).toFixed(0)}k` : usd(x));
  const qty = (x) => (!fin(x) ? "–" : Math.abs(x) >= 1e9 ? `${(x / 1e9).toFixed(2)}B` : Math.abs(x) >= 1e6 ? `${(x / 1e6).toFixed(2)}M` : Math.abs(x) >= 1e3 ? `${(x / 1e3).toFixed(1)}k` : x.toFixed(2));
  const when = (t) => (t ? new Date(t).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "–");
  const ago = (t) => { const m = Math.round((Date.now() - Date.parse(t)) / 60000); return !fin(m) ? "" : m < 60 ? `${m} min ago` : m < 2880 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`; };
  const short = (a) => (a ? `${a.slice(0, 5)}…${a.slice(-4)}` : "");
  const CHAIN = { solana: "Solana", eth: "Ethereum", binance: "Binance" };
  const ACTIVE = new Set(["accumulation_detected", "persistent_accumulation", "concentrated_ownership_risk"]);
  const TONE = { accumulation_detected: "good", persistent_accumulation: "good", concentrated_ownership_risk: "warn", investigating: "calm",
    accumulation_weakening: "warn", distribution_detected: "bad", data_insufficient: "calm", disqualified: "bad", archived: "calm" };
  const LABEL = { accumulation_detected: "Accumulation detected", persistent_accumulation: "Persistent accumulation", concentrated_ownership_risk: "Concentrated ownership risk",
    investigating: "Investigating", accumulation_weakening: "Accumulation weakening", distribution_detected: "Distribution detected", data_insufficient: "Data insufficient",
    disqualified: "Disqualified", archived: "Archived" };
  const explorer = (chain, a) => (chain === "solana" ? `https://solscan.io/account/${a}` : `https://etherscan.io/address/${a}`);
  const tokenLink = (r) => (r.chain === "solana" ? `https://solscan.io/token/${r.token}` : r.chain === "eth" ? `https://etherscan.io/token/${r.token}` : `https://www.binance.com/en/trade/${esc((r.symbol || "").toUpperCase())}_USDT`);
  let S = null, selected = null;

  // ------------------------------------------------------------------ summary
  function summary() {
    const wl = S.watchlist || [];
    const n = (st) => wl.filter((r) => r.state === st).length;
    const day = wl.filter((r) => r.detection && Date.now() - Date.parse(r.detection.at) < 86400e3).length;
    const P = (S.paper || {}).account || {};
    const age = (Date.now() - Date.parse(S.generated_at)) / 60000;
    $("summary").className = `banner ${age > 150 ? "warn" : "calm"}`;
    $("summary").innerHTML = `<b>${S.universe.tokens} DEX tokens watched</b> (${Object.entries(S.universe.by_chain || {}).map(([c, k]) => `${k} on ${CHAIN[c] || c}`).join(", ")}) plus Binance coins for exchange flow.
      <b>${wl.filter((r) => ACTIVE.has(r.state)).length} active candidates</b>: ${n("accumulation_detected")} detected, ${n("persistent_accumulation")} persistent, ${n("concentrated_ownership_risk")} concentrated;
      ${n("accumulation_weakening")} weakening, ${n("distribution_detected")} distributing, ${n("data_insufficient")} short of data. ${day} new in the last 24 h.
      Paper account <b>${usd(P.equity, 2)}</b> USDC. <span class="small muted">Updated ${esc(ago(S.generated_at))}${age > 150 ? " (out of date: the hourly job may have failed)" : ""}.</span>`;
    const st = $("status"); if (st) st.textContent = `Updated ${ago(S.generated_at)}`;
  }

  // ------------------------------------------------------------------ watchlist
  function persistence(r) {
    const w = (r.evidence || {}).windows || {};
    return [7, 14, 30, 60, 90].map((d) => {
      const x = w[d] || w[String(d)] || {};
      const cls = !x.complete ? "" : (x.net_qty || 0) > 0 ? "pos" : "neg";
      const h = !x.complete ? 6 : 10 + Math.min(18, Math.abs((x.share || 0) * 4000));
      return `<span class="${cls}" style="height:${h}px" title="${d} days: ${x.complete ? `net ${qty(x.net_qty)} tokens` : `only ${x.covered_days || 0} of ${d} days observed`}"></span>`;
    }).join("");
  }
  function table() {
    const fs = $("fState").value, fc = $("fChain").value, fq = $("fConf").value, q = $("fSearch").value.trim().toLowerCase();
    const rows = (S.watchlist || []).filter((r) => (fs === "all" || ACTIVE.has(r.state) || r.state === "accumulation_weakening") &&
      (!fc || r.chain === fc) && (!fq || r.confidence === fq) && (!q || (r.symbol || "").toLowerCase().includes(q)));
    if (!rows.length) {
      $("wlTable").innerHTML = `<p>${(S.watchlist || []).length ? "Nothing matches these filters." : "<b>No candidates yet.</b> The lab builds its own history from the day it started: Route A needs a week of observed swaps, persistence needs up to 90 days. Until then, honest silence is the expected result."}</p>`;
      return;
    }
    $("wlTable").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Token</th><th>State</th><th class="num">Score</th><th>Confidence</th><th>Route</th>
      <th class="num">7-day net buying</th><th class="num">Share of supply</th><th>Biggest buyer vs before</th><th class="num">Addresses buying / selling (14 d)</th>
      <th>7·14·30·60·90 d</th><th class="num">vs liquidity</th><th class="num">Price 14 d</th><th>First detected</th><th>Warnings</th></tr></thead><tbody>
      ${rows.map((r) => {
        const e = r.evidence || {}, w7 = (e.windows || {})[7] || (e.windows || {})["7"] || {}, w14 = (e.windows || {})[14] || (e.windows || {})["14"] || {};
        const b = (e.buyers || [])[0];
        const f = r.flow;
        return `<tr class="pick ${selected === r.key ? "sel" : ""}" data-key="${esc(r.key)}" tabindex="0">
          <td><b>${esc((r.symbol || "").toUpperCase())}</b><div class="small muted">${esc(CHAIN[r.chain] || r.chain)}</div></td>
          <td><span class="pill ${TONE[r.state] || ""}">${esc(LABEL[r.state] || r.state)}</span></td>
          <td class="num">${fin(r.score) ? r.score.toFixed(0) : "–"}</td><td>${esc(r.confidence || "–")}</td>
          <td>${(r.routes || []).map((x) => `<span class="kind" title="${esc(x.why)}">${esc(x.route)}</span>`).join(" ") || (f ? '<span class="kind">Exchange flow</span>' : "–")}</td>
          <td class="num">${f ? `taker buying ${share(f.share14)}` : `${qty(w7.net_qty)} <div class="small muted">${usd(w7.net_usd)}</div>`}</td>
          <td class="num">${f ? "–" : share(w7.share)}</td>
          <td>${b ? esc(b.label) : "–"}</td>
          <td class="num">${f ? "–" : `${w14.acc_wallets ?? "–"} / ${w14.dist_wallets ?? "–"}`}</td>
          <td><div class="bars" aria-hidden="true">${f ? "" : persistence(r)}</div></td>
          <td class="num">${f ? "–" : pct(w14.vs_liquidity)}</td>
          <td class="num">${pct(f ? f.chg14 : (e.price || {}).change)}</td>
          <td class="small">${r.detection ? esc(when(r.detection.at)) : "–"}</td>
          <td class="small">${(r.flags || []).length ? `${(r.flags || []).length} ⚠` : ""}</td></tr>`;
      }).join("")}</tbody></table></div>
      <p class="small muted">Bars: net buying in each window (green), net selling (red), grey where the lab hasn't watched long enough. Never ranked by share alone: supplies and data quality differ.</p>`;
    for (const tr of document.querySelectorAll("#wlTable tr.pick")) {
      const go = () => { selected = tr.dataset.key; table(); detail(); $("evidence").scrollIntoView({ behavior: "smooth", block: "start" }); };
      tr.addEventListener("click", go); tr.addEventListener("keydown", (ev) => { if (ev.key === "Enter") go(); });
    }
  }

  // ------------------------------------------------------------------ evidence
  function detail() {
    const r = (S.watchlist || []).find((x) => x.key === selected);
    if (!r) return;
    const e = r.evidence || {}, sc = e.score || {}, W = e.windows || {};
    const evs = (S.events || []).filter((x) => x.key === r.key);
    const f = r.flow;
    const winRows = [7, 14, 30, 60, 90].map((d) => { const x = W[d] || W[String(d)] || {}; return `<tr><td>${d} days</td><td class="num">${x.covered_days ?? 0} of ${d}</td><td class="num">${pct(x.coverage, 0)}</td>
      <td class="num">${qty(x.net_qty)}</td><td class="num">${usd(x.net_usd)}</td><td class="num">${share(x.share)}</td><td class="num">${x.buy_days ?? "–"}</td><td class="num">${x.acc_wallets ?? "–"} / ${x.dist_wallets ?? "–"}</td></tr>`; }).join("");
    $("evidenceBody").innerHTML = `<h3>${esc((r.symbol || "").toUpperCase())} <span class="small muted">on ${esc(CHAIN[r.chain] || r.chain)}</span> <span class="pill ${TONE[r.state] || ""}">${esc(LABEL[r.state] || r.state)}</span></h3>
      <p class="small"><a href="${tokenLink(r)}" target="_blank" rel="noopener">${r.chain === "binance" ? "Open on Binance" : `Token ${esc(short(r.token || ""))}`} ↗</a> · in this state since ${esc(when(r.since))} · last checked ${esc(ago(r.updated))}</p>
      ${r.detection ? `<div class="banner calm"><b>First detected ${esc(when(r.detection.at))}</b> (frozen, never moved) by route ${esc(r.detection.route)} at ${fin(r.detection.price) ? usd(r.detection.price, 6) : "an unknown price"}${fin((r.detection.score || {}).total) ? `, score ${r.detection.score.total.toFixed(0)} (version ${r.detection.version})` : ""}: ${esc((r.detection.evidence || []).join("; "))}</div>` : ""}
      ${f ? `<p><span class="kind">C</span> Exchange trading activity on Binance: market buyers took <b>${share(f.share14)}</b> of the volume over 14 days against <b>${share(f.share_base)}</b> in the 90 days before, while the price moved ${pct(f.chg14)}. <b>Exchange data never says who the buyers were</b>: this is buying pressure, not one investor.</p>` : `
      <p><b>Confidence: ${esc(r.confidence || "–")}</b> <span class="small muted">(${esc(r.confidence_why || "")})</span></p>
      <h4>Score ${fin(sc.total) ? sc.total.toFixed(0) : "–"} of 100 <span class="small muted">(version ${esc(sc.version)}, a research ranking, not a probability)</span></h4>
      <div class="table-wrap"><table><thead><tr><th>Part</th><th class="num">Points</th><th>From</th></tr></thead><tbody>
      ${Object.entries(sc.parts || {}).map(([k, p]) => `<tr><td>${esc(k.replace("_", " "))}</td><td class="num">${p.points} / ${p.max}</td><td class="small">${esc(p.why)}</td></tr>`).join("")}</tbody></table></div>
      <h4>Verified swaps by window <span class="kind">A</span></h4>
      <div class="table-wrap"><table><thead><tr><th>Window</th><th class="num">Days observed</th><th class="num">Swap coverage</th><th class="num">Net tokens</th><th class="num">Net $</th><th class="num">Share of supply</th><th class="num">Days with net buying</th><th class="num">Addresses buying / selling</th></tr></thead><tbody>${winRows}</tbody></table></div>
      <p class="small muted">Circulating supply: ${fin(e.circ) ? qty(e.circ) : "unknown"} (${esc(e.circ_src || "")}). Pool liquidity ${big(e.reserve)}. Price over 14 days ${pct((e.price || {}).change)}${(e.price || {}).repricing ? " (already repricing)" : ""}. Only swaps of $500+ are collected; bots and round trips are left out.</p>
      <h4>Biggest net buyers in 14 days <span class="kind">A</span> <span class="kind">B</span></h4>
      ${(e.buyers || []).length ? `<div class="table-wrap"><table><thead><tr><th>Address</th><th class="num">Net bought</th><th class="num">Net $</th><th class="num">Share of supply</th><th>Against what it held before</th></tr></thead><tbody>
        ${e.buyers.map((b) => `<tr><td class="addr"><a href="${explorer(r.chain, b.wallet)}" target="_blank" rel="noopener">${esc(short(b.wallet))}</a>${b.exchange ? ` <span class="pill warn">${esc(b.exchange)} custody</span>` : ""}</td><td class="num">${qty(b.net_qty)}</td><td class="num">${usd(b.net_usd)}</td><td class="num">${share(b.share)}</td>
        <td class="small">${esc(b.label)}${fin(b.before) ? ` (about ${qty(b.before)} before, ${qty(b.now)} now; transfers in between make this an estimate)` : ""}</td></tr>`).join("")}</tbody></table></div>
        <p class="small muted">Addresses, not people: one person can use many addresses and one address (an exchange) can hold many people's coins.</p>` : '<p class="muted small">No address net bought $1,000+ in the last 14 days.</p>'}`}
      <h4>Evidence against, and warnings</h4>
      <ul class="small">${(r.flags || []).map((x) => `<li>${esc(x)}</li>`).join("") || "<li>None recorded.</li>"}
        ${e.distribution ? `<li><b>${esc(e.distribution)}</b></li>` : ""}
        ${(e.to_exchange || []).slice(0, 5).map((t) => `<li>${esc(when(t.ts))}: ${qty(t.qty)} tokens moved from a watched address to ${esc(t.exchange)} (possible sale, not proof) · <a href="https://etherscan.io/tx/${esc(t.tx)}" target="_blank" rel="noopener">tx ↗</a></li>`).join("")}
        <li>Accumulation is not a forecast: many accumulated tokens never rise. Unlock schedules and project news aren't measured here.</li></ul>
      <h4>Status history</h4>
      ${evs.length ? `<div class="table-wrap"><table><thead><tr><th>When</th><th>Change</th><th>Why</th></tr></thead><tbody>${evs.map((x) => `<tr><td class="small">${esc(when(x.t))}</td><td>${esc(LABEL[x.from] || x.from || "new")} → <b>${esc(LABEL[x.to] || x.to)}</b></td><td class="small">${esc(x.reason)}</td></tr>`).join("")}</tbody></table></div>` : '<p class="small muted">No changes in the recent log.</p>'}`;
  }

  // ------------------------------------------------------------------ events, recovery, backtest, record, paper, sources
  function events() {
    const ev = (S.events || []).slice(0, 40);
    $("eventsBody").innerHTML = !ev.length ? "<p class='muted'>No changes yet.</p>" : `<div class="table-wrap"><table><thead><tr><th>When</th><th>Token</th><th>Change</th><th>What changed</th></tr></thead><tbody>
      ${ev.map((x) => `<tr><td class="small">${esc(when(x.t))}</td><td><b>${esc((x.symbol || "").toUpperCase())}</b></td><td><span class="pill ${TONE[x.to] || ""}">${esc(LABEL[x.to] || x.to)}</span></td><td class="small">${esc(x.reason)}</td></tr>`).join("")}</tbody></table></div>`;
  }
  function recovery() {
    const R = S.recovery || {};
    const rows = R.rows || [];
    if (!rows.length) { $("recoveryBody").innerHTML = "<p class='muted'>The daily screen hasn't run yet.</p>"; return; }
    $("recoveryBody").innerHTML = `<p class="small">${rows.length} of the ${R.screened} largest coins passed the screen (${esc(when(R.generated_at))}). Score out of 100: a research ranking with hard exclusions first, never a buy signal.</p>
      <div class="table-wrap"><table><thead><tr><th>Coin</th><th class="num">Score</th><th class="num">Below peak</th><th class="num">Market cap</th><th class="num">Fully diluted</th><th class="num">24 h volume</th><th>Usage (DefiLlama)</th><th>History</th><th>Confidence</th></tr></thead><tbody>
      ${rows.slice(0, 40).map((r) => `<tr class="pick" data-rec="${esc(r.id)}" tabindex="0"><td><b>${esc(r.symbol)}</b><div class="small muted">${esc(r.name)}</div></td><td class="num">${r.score.total.toFixed(0)}</td><td class="num">${pct(-r.drawdown, 0)}</td>
        <td class="num">${big(r.market_cap)}</td><td class="num">${big(r.fdv)}</td><td class="num">${big(r.volume)}</td><td class="small">${r.llama ? `TVL ${big(r.llama.tvl)}${fin(r.llama.fees_30d) ? `, fees ${big(r.llama.fees_30d)}/30 d` : ""}` : "not a DeFi protocol: not measured"}</td>
        <td class="small">${fin(r.history_days) ? `${Math.round(r.history_days / 365 * 10) / 10} years on Binance` : r.binance ? "on Binance" : "not on Binance"}</td><td>${esc(r.score.confidence)}</td></tr>
        <tr class="rec-detail" data-for="${esc(r.id)}" hidden><td colspan="9"><ul class="small">${Object.entries(r.score.parts).map(([k, p]) => `<li><b>${esc(k)}</b> ${p.points}/${p.max}: ${esc(p.why)}</li>`).join("")}</ul></td></tr>`).join("")}</tbody></table></div>
      <p class="small muted">Click a coin for its score parts. Catalysts and unlock dates have no reliable free source: they score 0 and say so. Peak price from CoinGecko; check that it wasn't a brief spike.</p>
      ${(R.rejected || []).length ? `<details><summary>Fell 75%+ but excluded (${R.rejected.length})</summary><ul class="small">${R.rejected.slice(0, 40).map((x) => `<li>${esc((x.symbol || "").toUpperCase())}: ${esc(x.why)}</li>`).join("")}</ul></details>` : ""}`;
    for (const tr of document.querySelectorAll("#recoveryBody tr.pick")) tr.addEventListener("click", () => { const d = document.querySelector(`tr.rec-detail[data-for="${CSS.escape(tr.dataset.rec)}"]`); if (d) d.hidden = !d.hidden; });
  }
  function backtest() {
    const B = S.backtest || {};
    if (!B.results) { $("backtestBody").innerHTML = "<p class='muted'>The weekly backtest hasn't run yet: it starts within the hour after this lab goes live.</p>"; return; }
    const tone = { good: "good", warn: "warn", bad: "bad" };
    $("backtestBody").innerHTML = `<p class="small">The recovery rule on <b>${B.coins}</b> Binance coins' daily history, <b>${B.delisted_coins} of them delisted</b> (kept so the test isn't flattered by survivors). At each month start: 2+ years of history, 75%+ below the previous 2-year high, $1M+ a day traded, 10%+ off its 30-day low. ${B.signals} signals. Costs ${pct(B.rules.cost)} a round trip. Price and volume only: past fundamentals aren't available.</p>
      <div class="table-wrap"><table><thead><tr><th>Held for</th><th class="num">Signals</th><th class="num">Made money</th><th class="num">Average</th><th class="num">Median</th><th class="num">Random coins</th><th class="num">Edge</th><th class="num">Luck test (t)</th><th class="num">Bitcoin</th><th class="num">Delisted before the end</th><th>Verdict</th></tr></thead><tbody>
      ${Object.entries(B.results).map(([h, r]) => { const a = r.all || {}; return `<tr><td>${h} days</td><td class="num">${a.n ?? 0}</td><td class="num">${fin(a.hit) ? `${Math.round(a.hit * 100)}%` : "–"}</td><td class="num">${pct(a.avg)}</td><td class="num">${pct(a.median)}</td><td class="num">${pct(a.random)}</td><td class="num">${pct(a.edge)}</td><td class="num">${fin(a.t_edge) ? a.t_edge.toFixed(1) : "–"}</td><td class="num">${pct(a.btc)}</td><td class="num">${a.delisted ?? "–"}</td><td><span class="pill ${tone[r.level] || ""}">${esc(r.verdict || "")}</span></td></tr>`; }).join("")}</tbody></table></div>
      <p class="small muted">Run ${esc(when(B.generated_at))}; inputs fingerprint ${esc(B.inputs_fingerprint)} (the same inputs always give the same result). "Held up" needs 30+ signals, a profit after costs, an edge over random coins with t of 2 or more, and an edge in the most recent third.</p>`;
  }
  function record() {
    const F = S.forward || {}, Sm = F.summary || {};
    const any = Object.values(Sm).some((x) => x.n);
    $("recordBody").innerHTML = `<p class="small">Each detection is judged after 7, 14, 30, 60 and 90 days: its price change (after a round trip's costs) against every other token watched on the same chain over the same days. Tokens that vanish are kept as "no price", never dropped. ${S.detections} detection${S.detections === 1 ? "" : "s"} so far.</p>
      ${any ? `<div class="table-wrap"><table><thead><tr><th>After</th><th class="num">Judged</th><th class="num">Rose</th><th class="num">Average</th><th class="num">Other tokens</th><th class="num">Edge</th><th class="num">Luck test (t)</th><th class="num">No price</th></tr></thead><tbody>
        ${Object.entries(Sm).map(([h, x]) => `<tr><td>${h} days</td><td class="num">${x.n}</td><td class="num">${fin(x.hit) ? `${Math.round(x.hit * 100)}%` : "–"}</td><td class="num">${pct(x.avg)}</td><td class="num">${pct(x.random)}</td><td class="num">${pct(x.edge)}</td><td class="num">${fin(x.t_edge) ? x.t_edge.toFixed(1) : "–"}</td><td class="num">${x.no_price}</td></tr>`).join("")}</tbody></table></div>`
        : "<p><b>Nothing to judge yet.</b> The first results arrive 7 days after the first detection; a verdict needs at least 30 detections judged at 30 days. This part is <b>Testing only</b> until then.</p>"}
      ${(F.rows || []).length ? `<details><summary>Latest results</summary><div class="table-wrap"><table><thead><tr><th>Token</th><th>Detected</th><th class="num">After</th><th class="num">Return</th><th class="num">Others</th><th class="num">Best / worst on the way</th></tr></thead><tbody>
        ${F.rows.slice(0, 30).map((x) => `<tr><td>${esc((x.symbol || "").toUpperCase())} <span class="kind">${esc(x.route)}</span></td><td class="small">${esc(when(x.at))}</td><td class="num">${x.horizon} d</td><td class="num">${x.status === "ok" ? pct(x.ret) : "no price"}</td><td class="num">${pct(x.random)}</td><td class="num">${pct(x.mfe)} / ${pct(x.mae)}</td></tr>`).join("")}</tbody></table></div></details>` : ""}`;
  }
  function paper() {
    const P = S.paper || {}, A = P.account || {};
    const ch = (A.equity || 0) - (A.start || 100);
    const plans = Object.entries(P.plans || {});
    $("paperBody").innerHTML = `<div class="stats">
        <div class="stat"><div class="k">Account value</div><div class="v ${ch > 0 ? "up" : ch < 0 ? "down" : ""}">${usd(A.equity, 2)}</div><div class="small muted">USDC, from ${usd(A.start, 2)}</div></div>
        <div class="stat"><div class="k">Cash</div><div class="v">${usd(A.cash, 2)}</div><div class="small muted">${usd(A.exposure, 2)} in positions</div></div>
        <div class="stat"><div class="k">Profit / loss</div><div class="v ${(A.realized || 0) + (A.unrealized || 0) >= 0 ? "up" : "down"}">${usd((A.realized || 0) + (A.unrealized || 0), 2)}</div><div class="small muted">${usd(A.realized, 2)} closed, ${usd(A.unrealized, 2)} open</div></div>
        <div class="stat"><div class="k">Planned open risk</div><div class="v">${usd(A.open_risk, 2)}</div><div class="small muted">limit 4% of the account; fees paid ${usd(A.fees, 2)}</div></div></div>
      <p class="small">Rules: risk 1% of the account per trade to a defensible invalidation level (the 14-day low, 2% lower), at most 5% of the account in one token and 4% planned risk in total, reward at least twice the risk after costs, no leverage, no averaging down. USDC ${fin(P.usdc_usd) ? `at ${P.usdc_usd.toFixed(4)} USD` : "price unavailable: new trades blocked"}. A watchlist entry never opens a trade by itself.</p>
      ${(P.positions || []).length ? `<h4>Open positions</h4><div class="table-wrap"><table><thead><tr><th>Token</th><th>Opened</th><th class="num">Entry</th><th class="num">Invalidation</th><th class="num">Target</th><th class="num">Cost</th></tr></thead><tbody>
        ${P.positions.map((p) => `<tr><td><b>${esc((p.symbol || "").toUpperCase())}</b> <span class="small muted">${esc(CHAIN[p.chain] || p.chain)}</span></td><td class="small">${esc(when(p.opened))}</td><td class="num">${usd(p.entry, 6)}</td><td class="num">${usd(p.stop, 6)}</td><td class="num">${usd(p.target, 6)}</td><td class="num">${usd(p.cost, 2)}</td></tr>`).join("")}</tbody></table></div>` : "<p class='muted'>No open positions: in cash.</p>"}
      ${(P.trades || []).length ? `<h4>Closed trades</h4><div class="table-wrap"><table><thead><tr><th>Token</th><th>Opened → closed</th><th class="num">Entry → exit</th><th class="num">Result</th><th>Why it closed</th></tr></thead><tbody>
        ${P.trades.map((t) => `<tr><td><b>${esc((t.symbol || "").toUpperCase())}</b></td><td class="small">${esc(when(t.opened))} → ${esc(when(t.closed))}</td><td class="num">${usd(t.entry, 6)} → ${usd(t.exit, 6)}</td><td class="num ${t.pnl >= 0 ? "up" : "down"}">${usd(t.pnl, 2)} (${pct(t.ret)})</td><td class="small">${esc(t.reason)}</td></tr>`).join("")}</tbody></table></div>` : ""}
      ${plans.length ? `<details><summary>Why each active candidate was or wasn't traded</summary><ul class="small">${plans.map(([k, p]) => `<li><b>${esc((((S.watchlist || []).find((r) => r.key === k) || {}).symbol || k).toUpperCase())}</b>: ${p.eligible ? `eligible, entry ${usd(p.entry, 6)}, invalidation ${usd(p.stop, 6)}, target ${usd(p.target, 6)}` : esc(p.why)}</li>`).join("")}</ul></details>` : ""}`;
  }
  function sources() {
    const R = S.sources || {};
    $("sourcesBody").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Source</th><th>Kind of evidence</th><th>What it gives</th><th>History</th><th>Limits</th><th>Status</th></tr></thead><tbody>
      ${Object.values(R).map((x) => `<tr><td><b>${esc(x.name)}</b><div class="small muted">${esc(x.auth)}</div></td><td class="small">${esc(x.category)}</td><td class="small">${esc(x.covers)}</td><td class="small">${esc(x.history)}</td><td class="small">${esc(x.limit)}</td>
        <td class="small">${!x.connected ? '<span class="pill warn">Not connected</span> add the free key to switch it on' : x.last_ok ? `<span class="pill good">Working</span> ${esc(ago(x.last_ok))}` : '<span class="pill calm">Not used yet</span>'}
        ${x.last_error ? `<div class="muted">Last error ${esc(ago(x.last_error_at))}: ${esc(x.last_error)}</div>` : ""}</td></tr>`).join("")}</tbody></table></div>
      ${(S.gaps_now || []).length ? `<p class="small">Gaps this hour (more $500+ swaps than the free feed returns): ${S.gaps_now.length} pool${S.gaps_now.length === 1 ? "" : "s"}. Their coverage is shown on each token.</p>` : ""}
      <details><summary>Tokens left out of the universe (${(S.universe.excluded || []).length} shown)</summary><ul class="small">${(S.universe.excluded || []).map((x) => `<li>${esc(x.symbol)} (${esc(CHAIN[x.chain] || x.chain)}): ${esc(x.why)}</li>`).join("")}</ul></details>`;
  }

  async function load() {
    try {
      const r = await fetch(`${DATA}snapshot.json?t=${Date.now()}`, { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      S = await r.json();
      summary(); table(); events(); recovery(); backtest(); record(); paper(); sources();
      if (selected) detail();
    } catch {
      $("summary").className = "banner warn";
      $("summary").innerHTML = "<b>The Accumulation lab's data isn't available yet.</b> It is produced every hour by the project's GitHub Actions; the first run starts shortly after the lab goes live.";
    }
  }
  for (const id of ["fState", "fChain", "fConf"]) $(id).addEventListener("change", () => S && table());
  $("fSearch").addEventListener("input", () => S && table());
  load(); setInterval(load, 10 * 60e3);
})();
