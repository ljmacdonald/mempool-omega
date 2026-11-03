/* Sniper lab (paper only): tokens that passed the launch checks just now, ones rejected and why, paper snipes in
   progress, results so far (passed vs rejected, per exit plan) and our real speed. Reads sniper/run.py's snapshot
   (every 15 minutes). Prices are labelled with their time; when the snapshot is old, the "just now" lists are hidden. */
(() => {
  "use strict";
  const DATA = window.OMEGA_SNIPER_DATA || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/data/sniper/";
  const STALE_MIN = 45;
  const CH = { solana: "Solana", bsc: "BNB Chain", ethereum: "Ethereum" };
  const Q = window.OmegaQuality;
  const $ = (id) => document.getElementById(id);
  const store = { get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } } };
  const state = { snap: null, stale: true, money: store.get("omega.snp.money", 100) };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fin = (x) => typeof x === "number" && Number.isFinite(x);
  const pct = (x, d = 1) => (fin(x) ? `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(d)}%` : "–");
  const pc0 = (x) => (fin(x) ? `${Math.round(x * 100)}%` : "–");
  const cls = (x) => (fin(x) ? (x > 0 ? "up" : "down") : "");
  const usd = (x) => (fin(x) ? `${x >= 0 ? "+" : "−"}$${Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "–");
  const money = (x) => (fin(x) ? (x >= 1e6 ? `$${(x / 1e6).toFixed(1)}M` : `$${Math.round(x / 1e3)}k`) : "–");
  const px = (x) => (fin(x) ? (x >= 1 ? x.toFixed(4) : x.toPrecision(4)) : "–");
  const when = (t) => new Date(t).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const mins = (m) => (!fin(m) ? "–" : m < 1 ? `${Math.round(m * 60)} s` : m < 90 ? `${m.toFixed(m < 10 ? 1 : 0)} min` : `${(m / 60).toFixed(1)} h`);
  const LV = { good: ["Held up", "good"], warn: ["Not proven yet", "warn"], bad: ["Failed", "bad"] };

  function summary(S) {
    const r = (S.results || {})[S.plan] || {}; const p = (S.plans || {})[S.plan] || {}; const c = S.counts || {};
    const sp = S.speed || {};
    $("summary").className = `banner ${r.level === "good" ? "good" : "warn"}`;
    $("summary").innerHTML = `<b>Paper only: nothing is ever bought.</b> We see a new token about <b>${mins(sp.median_delay_min)}</b> after its pool opens (typical; slowest 10%: ${mins(sp.p90_delay_min)}). Professional sniper bots buy in the first seconds, so every buy here pays them a ${((S.costs || {}).snipe * 100 || 2).toFixed(0)}% "late" cost on top of fees and slippage.
      ${c.closed ? `So far ${c.closed} paper snipes have finished. With the ${esc(p.name || "")} plan, tokens that <b>passed</b> the checks averaged <b>${pct((r.passed || {}).avg)}</b> after costs (${pc0((r.passed || {}).hit)} made money); tokens that were <b>rejected</b> averaged ${pct((r.rejected || {}).avg)}${fin(r.rug_rejected) ? `, and ${pc0(r.rug_rejected)} of them rugged (vs ${pc0(r.rug_passed)} of passed ones)` : ""}${r.unsellable ? ` (${r.unsellable} rejected tokens that looked impossible to sell are left out: on paper they'd look like winners, in reality you couldn't cash out)` : ""}. Verdict: <b>${esc(LV[r.level || "warn"][0])}</b>.`
        : `Results start building up now: each paper snipe is judged once its 12-hour window has passed. ${c.checked || 0} tokens checked so far, ${c.passed || 0} passed.`}`;
  }

  function calc(x, plan) {
    const a = state.money; const c = x.cost_rt || 0;
    const rows = [["Take profit hit", plan.tp], ["Safety exit hit", -plan.sl], ["Rug pull (−95%)", -0.95]];
    return `<div class="calc"><h3>With $${a.toLocaleString()} on paper (${esc(plan.name)} plan: +${Math.round(plan.tp * 100)}% / −${Math.round(plan.sl * 100)}% / ${plan.hours} h)</h3>
      <table><tbody>${rows.map(([k, m]) => { const v = a * ((1 + m) * (1 - c) - 1); return `<tr><td>${esc(k)}</td><td class="num ${cls(v)}">${usd(v)}</td></tr>`; }).join("")}</tbody></table>
      <p class="small muted">All-in costs for a $${a.toLocaleString()} round trip here: about ${(c * 100).toFixed(1)}% (pool fee, price impact, sandwich bots, taxes, gas and the late-buyer cost).</p></div>`;
  }
  function idea(x, S) {
    const plan = S.plans[x.plan] || S.plans[S.plan]; const q = S.quality;
    return `<article class="card idea" data-alert-key="idea-${esc(x.id)}"><div class="idea-head"><h3><span class="rank">#${x.rank}</span>${esc(x.symbol)} <span class="muted small">${esc(x.name || "")} · ${esc(CH[x.chain])} · ${esc(x.dex)}</span></h3>
      <div class="score"><b>${(x.score || 0).toFixed(1)}</b><span class="muted">/ 10</span> <span class="pill ${Q.gradeClassFor(x.grade, q)}">${esc(Q.gradeWord(x.grade, q, x.rank))}</span> <span class="pill ${x.evidence === "Held up in paper tests" ? "good" : "warn"}">${esc(x.evidence)}</span></div></div>
      <div class="facts">
        <div class="fact"><div class="k">Pool opened</div><div class="v">${mins(x.delay_min)}</div><div class="d muted">before our paper buy</div></div>
        <div class="fact"><div class="k">Paper buy at</div><div class="v">${px(x.entry)}</div><div class="d muted">${when(Date.parse(x.entry_t))}</div></div>
        <div class="fact"><div class="k">Real money in pool</div><div class="v">${money(x.reserve)}</div><div class="d muted">SOL/ETH/BNB/stable side</div></div>
        <div class="fact"><div class="k">Chance it makes money</div><div class="v">${pc0(x.prob)}</div><div class="d muted">from ${x.hist_n} finished paper snipes</div></div>
        <div class="fact"><div class="k">Expected, after costs</div><div class="v ${cls(x.exp_r_adj)}">${x.exp_r_adj >= 0 ? "+" : "−"}${Math.abs(x.exp_r_adj).toFixed(2)}R</div><div class="d muted">per $1 put at risk</div></div>
      </div>
      ${(x.warn_list || []).length ? `<ul class="warnings">${x.warn_list.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
      <details class="checks"><summary>Passed ${x.checks.length} scam and rug-pull checks</summary><ul>${x.checks.map((c) => `<li><span class="ok">✓</span><span>${esc(c)}</span></li>`).join("")}</ul></details>
      ${calc(x, plan)}
      <p class="small"><a href="${esc(x.url)}" target="_blank" rel="noopener">Open on DexScreener</a> · always check the token address there: fakes copy names.</p></article>`;
  }
  function ideas(S) {
    $("ideasBanner").innerHTML = Q.ideasBanner(S.quality);
    if (state.stale) { $("ideas").innerHTML = `<div class="banner warn">The latest check is more than ${STALE_MIN} minutes old; new tokens move too fast to show old ones.</div>`; return; }
    const list = S.ideas || [];
    $("ideas").innerHTML = list.length ? list.map((x) => idea(x, S)).join("")
      : `<div class="banner calm"><b>No new token passed the checks in the latest run.</b> Most brand-new tokens fail: see below for why. Checked every 15 minutes.</div>`;
  }
  function rejected(S) {
    const r = S.rejected || [];
    $("rejected").innerHTML = state.stale ? `<p class="muted">Hidden: the latest check is out of date.</p>` : !r.length ? `<p class="muted">Nothing was rejected in the latest run.</p>`
      : `<div class="table-wrap"><table><thead><tr><th>Token</th><th>Network</th><th class="num">Pool opened</th><th class="num">Real money</th><th>Why it was rejected</th></tr></thead><tbody>
        ${r.map((x) => `<tr><td><b>${esc(x.symbol)}</b></td><td>${esc(CH[x.chain])}</td><td class="num">${mins(x.delay_min)} ago</td><td class="num">${money(x.reserve)}</td><td class="small" style="white-space:normal">${x.reasons.map(esc).join("<br>")}</td></tr>`).join("")}
        </tbody></table></div><p class="small muted">Rejected tokens are paper-bought too, so the results below can show what the checks saved you from.</p>`;
  }
  function open(S) {
    const o = S.open || [];
    $("open").innerHTML = !o.length ? `<p class="muted">No paper snipes open.</p>` : `<div class="table-wrap"><table><thead><tr><th>Token</th><th>Checks</th><th>Bought</th><th class="num">Entry price</th><th class="num">Price at last check</th><th class="num">Take profit / safety exit</th><th class="num">Since the buy, after costs</th><th class="num">Pool money since the buy</th><th></th></tr></thead><tbody>
      ${o.map((x) => { const pl = (S.plans || {})[S.plan] || { tp: 0, sl: 0 };
        return `<tr><td><b>${esc(x.symbol)}</b> <span class="muted small">${esc(CH[x.chain])}</span></td><td>${x.verdict === "pass" ? '<span class="pill good">passed</span>' : '<span class="pill bad">rejected</span>'}</td><td class="small">${when(Date.parse(x.entry_t))} · ${mins(x.delay_min)} after launch</td><td class="num"><b>${px(x.entry)}</b></td><td class="num">${px(x.last)} <span class="muted small">${when(Date.parse(x.last_t))}</span></td><td class="num small"><span class="up">${px(x.entry * (1 + pl.tp))}</span> / <span class="down">${px(x.entry * (1 - pl.sl))}</span></td><td class="num ${cls(x.ret)}">${pct(x.ret)}</td><td class="num ${cls(x.pool_chg)}">${pct(x.pool_chg, 0)}${fin(x.pool_chg) && x.pool_chg < -0.5 ? ' <span class="pill bad">being pulled</span>' : ""}</td><td><a class="small" href="${esc(x.url)}" target="_blank" rel="noopener">chart</a></td></tr>`; }).join("")}
      </tbody></table></div><p class="small muted">Prices from the latest 15-minute check, not live. Each snipe is judged on 5-minute candles once 12 hours have passed.</p>`.replace("Prices from the latest 15-minute check, not live.", `Prices from the latest 15-minute check, not live. Take profit and safety exit are for the ${esc(((S.plans || {})[S.plan] || {}).name || "")} plan.`);
  }
  function results(S) {
    const rows = Object.entries(S.plans || {}).map(([k, p]) => { const r = (S.results || {})[k] || {}; const a = r.passed || {}, j = r.rejected || {};
      return `<tr${k === S.plan ? ' style="font-weight:600"' : ""}><td>${esc(p.name)}${k === S.plan ? ' <span class="pill calm">used for grades</span>' : ""}<div class="small muted">+${Math.round(p.tp * 100)}% / −${Math.round(p.sl * 100)}% / ${p.hours} h</div></td>
        <td class="num">${a.n || 0}</td><td class="num">${pc0(a.hit)}</td><td class="num ${cls(a.avg)}">${pct(a.avg)}</td><td class="num ${cls(j.avg)}">${pct(j.avg)} <span class="muted small">(${j.n || 0})</span></td>
        <td class="num">${pc0(r.rug_passed)} / ${pc0(r.rug_rejected)}</td><td><span class="pill ${LV[r.level || "warn"][1]}">${LV[r.level || "warn"][0]}</span></td></tr>`; }).join("");
    const rec = (S.recent || []).slice(0, 12);
    $("results").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Exit plan</th><th class="num">Passed: snipes</th><th class="num">Made money</th><th class="num">Average after costs</th><th class="num">Rejected ones</th><th class="num">Rugged: passed / rejected</th><th>Verdict</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="small muted">A plan "holds up" only with 30+ finished snipes, a profit after costs, a clear edge over buying the rejected tokens at the same hours, and still in the most recent third. Rug = fell 90% from our buy or the pool lost 90% of its money within 12 hours.</p>
      ${rec.length ? `<details><summary>Latest finished paper snipes</summary><div class="table-wrap"><table><thead><tr><th>Bought</th><th>Token</th><th>Checks</th><th class="num">Entry → exit price</th><th class="num">Result after costs</th><th>Why it closed</th></tr></thead><tbody>${rec.map((x) => `<tr><td class="small">${when(Date.parse(x.entry_t))}</td><td>${esc(x.symbol)} <span class="muted small">${esc(CH[x.chain])}</span></td><td>${x.verdict === "pass" ? "passed" : "rejected"}</td><td class="num small">${px(x.entry)} → ${px(x.exit)}</td><td class="num ${cls(x.net)}">${pct(x.net)}</td><td class="small">${esc(x.reason || "")}${x.rug ? ' <span class="pill bad">rugged</span>' : ""}</td></tr>`).join("")}</tbody></table></div></details>` : ""}
      ${Q.gradeTable(S.quality, "How each grade actually turned out")}`;
  }
  function speed(S) {
    const sp = S.speed || {};
    $("speed").innerHTML = `<div class="stats"><div class="stat"><div class="k">Our typical delay</div><div class="v">${mins(sp.median_delay_min)}</div><div class="small muted">after the pool opens</div></div>
      <div class="stat"><div class="k">Slowest 10%</div><div class="v">${mins(sp.p90_delay_min)}</div></div><div class="stat"><div class="k">Professional snipers</div><div class="v">&lt; 1 s</div><div class="small muted">same block as the launch</div></div>
      <div class="stat"><div class="k">Typical all-in cost</div><div class="v">${pc0((S.costs || {}).median_cost_rt)}</div><div class="small muted">per round trip</div></div></div>
      <p class="small">Why we can't win the speed race: top bots run their own servers next to the blockchain's nodes, pay for priority (Jito bundles on Solana, private builders on Ethereum) and buy in the same block the pool opens, often before any safety check could finish. This lab runs on free GitHub computers every 15 minutes. So the honest question it answers is different: <b>after the bots have bought, is there still money in safe-looking new tokens, and do our checks help?</b> The numbers above answer it, after every cost.</p>`;
  }

  async function load() {
    try {
      const r = await fetch(`${DATA}snapshot.json?t=${Date.now()}`, { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      const S = await r.json();
      const age = (Date.now() - Date.parse(S.generated_at)) / 60000;
      state.snap = S; state.stale = !(age <= STALE_MIN);
      $("status").textContent = `Checked ${Math.round(age)} min ago`; $("status").className = `status${state.stale ? " stale" : ""}`;
      summary(S); ideas(S); rejected(S); open(S); results(S); speed(S);
    } catch {
      $("summary").className = "banner warn";
      $("summary").textContent = "Couldn't load the sniper lab data. It's produced by the project's GitHub Actions; try again in a few minutes.";
    }
  }
  $("money").value = String(state.money);
  $("money").addEventListener("change", (e) => { state.money = Math.max(1, +e.target.value || 100); store.set("omega.snp.money", state.money); if (state.snap) ideas(state.snap); });
  load();
  setInterval(load, 5 * 60 * 1000);
})();
