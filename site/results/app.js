/* Results (DECISIONS D98): every section's own paper record on one screen, from infra/summary.py (state/summary.json,
   refreshed every 15 minutes). Shows exactly what each page publishes; nothing is recomputed or filtered here. */
(() => {
  "use strict";
  const DATA = window.OMEGA_REPO || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/main/state/";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fin = (x) => typeof x === "number" && Number.isFinite(x);
  const val = (x, unit) => (!fin(x) ? "–" : unit === "R" ? `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(2)} R` : `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(2)}%`);
  const ago = (m) => (!fin(m) ? "never" : m < 60 ? `${m} min ago` : m < 48 * 60 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`);
  const PILL = { ahead: ["Ahead", "good"], even: ["Even", "calm"], behind: ["Behind", "bad"], early: ["Too early", "warn"] };
  const ORDER = { ahead: 0, even: 1, behind: 2, early: 3 };
  const BASE = new URL("../", location.href).href;
  const VM = { beats: ["Beats the market", "good"], level: ["No clear difference", "calm"], lags: ["Lags the market", "bad"], early: ["Too early", "warn"] };
  const vm = (s) => {
    const v = s.vs_market;
    if (!v) return '<span class="small muted">–</span>';
    const [t, c] = VM[v.verdict] || [v.verdict, ""];
    return `<span class="pill ${c}">${t}</span><div class="small muted">${val(v.edge, s.unit)} a trade${fin(v.hedged) ? ` · hedged ${val(v.hedged, s.unit)}` : ""}</div>`;
  };

  function render(S) {
    const secs = S.sections || [];
    const ch = secs.filter((s) => s.key.startsWith("challenge"));
    const rest = secs.filter((s) => !s.key.startsWith("challenge"));
    const count = (k) => rest.filter((s) => s.status === k).length;
    $("summary").className = `banner ${count("ahead") ? "good" : "calm"}`;
    $("summary").innerHTML = `<b>${rest.length} sections, recorded live on paper.</b> ${count("ahead")} ahead, ${count("even")} even, ${count("behind")} behind, ${count("early")} too early to tell.
      We show the losers too: that is the point. <span class="small muted">Updated ${esc(ago(Math.round((Date.now() - Date.parse(S.generated_at)) / 60000)))}${S.ok ? "" : ` · <a href="${BASE}status/">some data is out of date</a>`}.</span>`;
    $("challenges").innerHTML = `<div class="stats">${ch.map((s) => `<a class="stat" href="${BASE}${esc(s.path)}" style="text-decoration:none;color:inherit">
        <div class="k">${esc(s.name)}</div><div class="v ${fin(s.equity) ? (s.equity >= 100 ? "up" : "down") : ""}">${fin(s.equity) ? `$${s.equity.toFixed(2)}` : "–"}</div>
        <div class="small muted">${s.n} finished trade${s.n === 1 ? "" : "s"}${s.open ? `, ${s.open} open` : ", in cash"}${s.paused ? " · paused" : ""} · from $100</div></a>`).join("")}</div>
      <p class="small muted">$100 paper accounts, each trading by fixed rules: the site's own signals, Solana arbitrage, and funding income. Capital first, paying every real cost. Staying in cash is part of the plan.</p>`;
    rest.sort((a, b) => ORDER[a.status] - ORDER[b.status] || b.n - a.n);
    $("sections").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Section</th><th>Status</th><th class="num">Finished</th><th class="num">Average per trade</th><th class="num">Random picks</th><th>Picks vs market</th><th>Updated</th></tr></thead><tbody>
      ${rest.map((s) => { const [label, tone] = PILL[s.status] || [s.status, ""]; return `<tr>
        <td><a href="${BASE}${esc(s.path)}"><b>${esc(s.name)}</b></a>${s.note ? `<div class="small muted">${esc(s.note)}</div>` : ""}</td>
        <td><span class="pill ${tone}">${label}</span></td><td class="num">${s.n.toLocaleString()}</td>
        <td class="num ${fin(s.avg) ? (s.avg >= 0 ? "up" : "down") : ""}">${val(s.avg, s.unit)}</td><td class="num">${val(s.random, s.unit)}</td><td>${vm(s)}</td>
        <td class="small">${s.market_closed ? "market closed" : esc(ago(s.age_min))}${s.stale ? ' <span class="pill warn">out of date</span>' : ""}</td></tr>`; }).join("")}
      </tbody></table></div>`;
    const st = document.querySelector(".top .status") || $("status");
    if (st) st.textContent = `Updated ${ago(Math.round((Date.now() - Date.parse(S.generated_at)) / 60000))}`;
  }

  async function load() {
    try {
      const r = await fetch(`${DATA}summary.json?t=${Date.now()}`, { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      render(await r.json());
    } catch {
      $("summary").className = "banner warn";
      $("summary").textContent = "The results summary isn't available right now. It refreshes every 15 minutes: try again soon.";
    }
  }
  // Proven or testing only (D115) and the market filter's test (D116), from site/gate.js
  async function proof() {
    const G = window.OMEGA_GATE;
    if (!G) return;
    await G.reload();
    const P = G.proof;
    if (!P) { $("proofTable").innerHTML = '<p class="muted">Not available right now: it refreshes every 15 minutes.</p>'; return; }
    const us = [...(P.units || [])].sort((a, b) => (b.proven - a.proven) || ((b.n || 0) - (a.n || 0)));
    const t = (x) => (fin(x) ? x.toFixed(1) : "–");
    $("proofTable").innerHTML = `<p><b>${(P.proven_ids || []).length} of ${us.length}</b> parts of the product have passed.</p><div class="table-wrap"><table class="proof-table"><thead><tr><th>Part</th><th>Status</th><th class="num">Trades</th><th class="num">Average</th><th class="num">vs random</th><th class="num">Luck test (t)</th><th>Why</th></tr></thead><tbody>
      ${us.map((u) => `<tr class="${u.proven ? "is-proven" : ""}"><td><a href="${BASE}${esc(u.path || "")}">${esc(u.name || u.id)}</a><div class="small muted">${esc(u.source || "")}</div></td>
        <td><span class="pill ${u.proven ? "good" : "warn"}">${u.proven ? "Proven" : "Testing only"}</span></td><td class="num">${fin(u.n) ? u.n.toLocaleString() : "–"}</td>
        <td class="num ${fin(u.avg) ? (u.avg >= 0 ? "up" : "down") : ""}">${val(u.avg, u.unit)}</td><td class="num">${val(u.edge, u.unit)}</td><td class="num">${t(u.t_edge)}</td>
        <td class="small">${esc(u.reason || "")}</td></tr>`).join("")}</tbody></table></div>`;
    const M = G.market;
    if (M && M.test) {
      const T = M.test;
      $("marketTest").innerHTML = `<h3>The market filter</h3><p class="small">Rule, fixed before testing: pause crypto buy ideas while Bitcoin is below its 7-day average. It is switched on only if, on every finished crypto idea, the ideas it lets through did better than the ones it would block, by more than luck, in both halves of the record.</p>
        <p><span class="pill ${T.enabled ? "good" : "calm"}">${T.enabled ? "On" : "Off"}</span> ${esc(T.verdict || "")}${fin(T.n) ? ` <span class="small muted">(${T.n.toLocaleString()} ideas${T.from ? `, ${esc(String(T.from).slice(0, 10))} to ${esc(String(T.to).slice(0, 10))}` : ""}; re-tested every 15 minutes)</span>` : ""}</p>`;
    }
  }
  load(); proof(); setInterval(() => { load(); proof(); }, 5 * 60e3);
})();
