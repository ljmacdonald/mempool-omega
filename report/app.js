/* Weekly report (DECISIONS D102): each week's finished paper trades per section, from infra/report.py
   (state/reports/weekly.json, refreshed every 15 minutes). Shows only what the file says; nothing is recomputed. */
(() => {
  "use strict";
  const DATA = window.OMEGA_REPO || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/main/state/";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fin = (x) => typeof x === "number" && Number.isFinite(x);
  const val = (x, unit) => (!fin(x) ? "–" : unit === "R" ? `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(2)} R` : `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(2)}%`);
  const day = (t) => new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  const when = (t) => new Date(t).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const PILL = { ahead: ["Ahead", "good"], even: ["Even", "calm"], behind: ["Behind", "bad"], early: ["Too early", "warn"] };
  const BASE = new URL("../", location.href).href;
  const money = (x) => (fin(x) ? `$${x.toFixed(2)}` : "–");

  function weekTable(w) {
    const ch = w.sections.filter((s) => s.key.startsWith("challenge"));
    const rest = w.sections.filter((s) => !s.key.startsWith("challenge"));
    const finished = rest.reduce((a, s) => a + (s.trades || 0), 0);
    const better = rest.filter((s) => s.trades && fin(s.avg) && fin(s.random) && s.avg > s.random).length;
    const withTrades = rest.filter((s) => s.trades && fin(s.avg)).length;
    return `<p>${finished.toLocaleString()} paper trades finished across ${withTrades} section${withTrades === 1 ? "" : "s"}; ${better} of them did better than random picks this week.</p>
      ${ch.length ? `<div class="stats">${ch.map((s) => `<a class="stat" href="${BASE}${esc(s.path)}" style="text-decoration:none;color:inherit">
        <div class="k">${esc(s.name)}</div><div class="v ${fin(s.equity_to) && fin(s.equity_from) ? (s.equity_to >= s.equity_from ? "up" : "down") : ""}">${money(s.equity_to)}</div>
        <div class="small muted">from ${money(s.equity_from)} at the start of the week · ${s.trades ?? "–"} trade${s.trades === 1 ? "" : "s"} finished</div></a>`).join("")}</div>` : ""}
      <div class="table-wrap"><table><thead><tr><th>Section</th><th class="num">Finished this week</th><th class="num">Average per trade</th><th class="num">Random picks</th><th>Whole record</th></tr></thead><tbody>
      ${rest.map((s) => { const [label, tone] = PILL[s.status] || [s.status || "–", ""]; return `<tr>
        <td><a href="${BASE}${esc(s.path)}">${esc(s.name)}</a></td>
        <td class="num">${s.trades == null ? '<span class="muted" title="The record was cleaned up during this week">–</span>' : s.trades.toLocaleString()}</td>
        <td class="num ${fin(s.avg) ? (s.avg >= 0 ? "up" : "down") : ""}">${val(s.avg, s.unit)}</td><td class="num">${val(s.random, s.unit)}</td>
        <td><span class="pill ${tone}">${label}</span></td></tr>`; }).join("")}
      </tbody></table></div>`;
  }

  function render(D) {
    const weeks = D.weeks || [];
    const now = D.so_far;
    $("summary").className = "banner calm";
    $("summary").innerHTML = weeks.length
      ? `<b>${weeks.length} finished week${weeks.length === 1 ? "" : "s"} on record.</b> Every section's paper trades, week by week, losers included.`
      : `<b>The first full weekly report arrives on Monday ${esc(day(new Date(Date.parse(now.starts) + 7 * 864e5)))}.</b> Records started being frozen on ${esc(when(now.from))}; until then, this week so far is shown below.`;
    if (now) {
      $("soFarTitle").textContent = `This week so far (since ${when(now.from)})`;
      $("soFar").innerHTML = weekTable(now);
    }
    $("weeks").innerHTML = !weeks.length ? `<p class="muted">No finished weeks yet.</p>`
      : weeks.map((w) => `<details ${w === weeks[0] ? "open" : ""}><summary><b>Week of ${esc(day(w.starts))}</b> <span class="small muted">(${esc(when(w.from))} to ${esc(when(w.to))})</span></summary>${weekTable(w)}</details>`).join("");
    const st = document.querySelector(".top .status") || $("status");
    if (st) st.textContent = `Updated ${when(D.generated_at)}`;
  }

  async function load() {
    try {
      const r = await fetch(`${DATA}reports/weekly.json?t=${Date.now()}`, { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      render(await r.json());
    } catch {
      $("summary").className = "banner warn";
      $("summary").textContent = "The weekly report isn't available right now. It refreshes every 15 minutes: try again soon.";
    }
  }
  load(); setInterval(load, 10 * 60e3);
})();
