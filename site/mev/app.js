/* Arbitrage lab (DECISIONS D103): shadow-only MEV survival research, from mev/shadow.py (data branch mev/snapshot.json,
   refreshed hourly). Shows what the file says; nothing is recomputed here. */
(() => {
  "use strict";
  const DATA = window.OMEGA_MEV_DATA || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/data/mev/";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fin = (x) => typeof x === "number" && Number.isFinite(x);
  const sol = (lamports, dp = 6) => (fin(lamports) ? `${lamports < 0 ? "−" : ""}${(Math.abs(lamports) / 1e9).toFixed(dp)} SOL` : "–");
  const pct = (x) => (fin(x) ? `${(x * 100).toFixed(0)}%` : "–");
  const when = (t) => (t ? new Date(t).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "–");
  const METHOD = { rpc_plain: "Normal transaction (baseline)", jito_bundle: "Jito bundle with a tip", jito_dontfront: "Jito + DontFront marker" };
  const VERDICT = {
    INSUFFICIENT_EVIDENCE: ["Not enough evidence", "warn"], NOT_VIABLE: ["Not viable", "bad"], PROMISING_IN_SHADOW: ["Promising on paper", "good"],
  };
  const pill = (v) => { const [t, c] = VERDICT[v] || [v, "calm"]; return `<span class="pill ${c}">${esc(t)}</span>`; };

  function render(S) {
    const c = S.counts || {};
    const [vt] = VERDICT[S.verdict] || [S.verdict];
    $("summary").className = `banner ${S.verdict === "PROMISING_IN_SHADOW" ? "good" : S.verdict === "NOT_VIABLE" ? "warn" : "calm"}`;
    $("summary").innerHTML = `<b>Verdict: ${esc(vt)}.</b> ${c.sessions || 0} hourly search${c.sessions === 1 ? "" : "es"} so far checked ${(c.candidates || 0).toLocaleString()} round trips of ${esc(S.trade_sol)} SOL; `
      + `${(c.opportunities || 0).toLocaleString()} would have paid after every cost when first seen. Transactions sent: <b>0</b> (shadow mode). Landing: <b>not enough evidence</b>.`;

    const delays = S.delays || [];
    $("survival").innerHTML = `<p>For each way of sending a trade: of the opportunities that would have paid when first seen, how many still paid after each delay (after that method's own costs). The lower bound is the cautious estimate used for decisions.</p>
      <div class="table-wrap"><table><thead><tr><th>Method</th>${delays.map((d) => `<th class="num">After ${d} s</th>`).join("")}<th>Verdict (fastest re-check)</th></tr></thead><tbody>
      ${Object.entries(S.survival || {}).map(([m, rows]) => `<tr><td>${esc(METHOD[m] || m)}</td>
        ${rows.map((r) => `<td class="num">${r.all.n ? `${pct(r.all.rate)} <span class="small muted">of ${r.all.n} · low ${pct(r.all.rate_low)}</span>` : '<span class="muted">none yet</span>'}</td>`).join("")}
        <td>${pill(rows[0]?.verdict)}<div class="small muted">${esc(rows[0]?.reason)}</div></td></tr>`).join("")}
      </tbody></table></div>`;

    const f = S.fees_now || {};
    const tf = f.tip_floor || {};
    const cost = f.costs || {};
    $("costs").innerHTML = `<ul class="rules">
        <li><b>Network fee:</b> ${sol(f.base_fee)} per signature, paid whenever a transaction lands, even if it fails.</li>
        <li><b>Priority fee:</b> ${esc((f.cu_price_p50 ?? "–").toLocaleString())} micro-lamports per compute unit × ${esc((f.cu_limit || 0).toLocaleString())} units = ${sol(f.priority_fee_p50)} ${f.priority_floor_used ? `<span class="pill warn">assumed floor</span> <span class="small muted">busy pools mostly reported zero, so a minimum of ${esc((f.priority_floor || 0).toLocaleString())} is assumed for a competitive trade</span>` : "(median paid on busy SOL pools)"}.</li>
        <li><b>Jito tips that landed recently:</b> half were under ${sol(Math.round((tf.landed_tips_50th_percentile || 0) * 1e9))}, three quarters under ${sol(Math.round((tf.landed_tips_75th_percentile || 0) * 1e9))}, 95% under ${sol(Math.round((tf.landed_tips_95th_percentile || 0) * 1e9))}. This lab bids the 75th percentile: ${sol(f.tip_used)}.</li>
      </ul>
      <div class="table-wrap"><table><thead><tr><th>Method</th><th class="num">Cost if it lands and pays</th><th class="num">Cost if it lands and fails</th><th class="num">Cost if it never lands</th></tr></thead><tbody>
      ${Object.entries(cost).map(([m, x]) => `<tr><td>${esc(METHOD[m] || m)}</td><td class="num">${sol(x.on_success)}</td><td class="num">${sol(x.on_revert)}</td><td class="num">${sol(x.on_miss)}</td></tr>`).join("")}
      </tbody></table></div>
      <p class="small muted">Pool fees and price impact are already inside the quoted prices and are not counted again.</p>`;

    const ss = (S.sessions || []).slice().reverse();
    $("sessions").innerHTML = !ss.length ? '<p class="muted">The first search runs within the hour.</p>' : `<div class="table-wrap"><table><thead><tr><th>Search</th><th class="num">Round trips</th><th class="num">Would have paid</th><th>Best round trip (before network costs)</th><th class="num">Price requests</th></tr></thead><tbody>
      ${ss.slice(0, 24).map((s) => `<tr><td>${esc(when(s.at))}</td><td class="num">${s.candidates}</td><td class="num">${s.opportunities}</td>
        <td>${s.best ? `<span class="${s.best.gross >= 0 ? "up" : "down"}">${sol(s.best.gross)}</span> <span class="small muted">${esc(s.best.token)}: ${esc(s.best.buy_dex)} → ${esc(s.best.sell_dex)}</span>` : "–"}</td>
        <td class="num">${s.quotes}${s.refused ? ` <span class="small muted">(${s.refused} refused)</span>` : ""}</td></tr>`).join("")}
      </tbody></table></div>`;

    const rs = S.routes || [];
    $("routes").innerHTML = !rs.length ? '<p class="muted">No round trip has paid after costs yet. That is the usual result: the gaps between big exchanges are closed by bots almost instantly.</p>'
      : `<div class="table-wrap"><table><thead><tr><th>Route</th><th class="num">Opportunities</th><th class="num">Still there after 1 s (Jito)</th><th class="num">Best seen</th></tr></thead><tbody>
        ${rs.map((r) => `<tr><td>${esc(r.route)}</td><td class="num">${r.n}</td><td class="num">${r.survived_1s}</td><td class="num">${sol(r.best_gross)}</td></tr>`).join("")}</tbody></table></div>`;

    const g = S.gate || { conditions: [] };
    $("gate").innerHTML = `<p>Real trading stays off until <b>every</b> condition below is met, and the owner's bankroll and daily loss limits apply on top, whatever the numbers say.</p>
      <ul class="gate">${g.conditions.map((x) => `<li>${x.ok ? '<span class="pill good">Yes</span>' : '<span class="pill bad">No</span>'} ${esc(x.name)}${x.ok ? "" : ` <span class="small muted">${esc(x.why)}</span>`}</li>`).join("")}</ul>`;

    const st = document.querySelector(".top .status") || $("status");
    if (st) st.textContent = `Updated ${when(S.generated_at)}`;
  }

  async function load() {
    try {
      const r = await fetch(`${DATA}snapshot.json?t=${Date.now()}`, { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      render(await r.json());
    } catch {
      $("summary").className = "banner warn";
      $("summary").textContent = "The arbitrage lab's data isn't available yet. The first search runs within the hour.";
    }
  }
  load(); setInterval(load, 10 * 60e3);
})();
