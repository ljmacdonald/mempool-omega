/* How each grade actually turned out, and whether the grades can be trusted right now (from scanner/quality.py,
   inside every page's scoreboard JSON). Shared by all pages. */
(function (root) {
  "use strict";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (x, d = 2) => (Number.isFinite(x) ? `${x >= 0 ? "+" : ""}${(x * 100).toFixed(d)}%` : "–");
  const R = (x) => (Number.isFinite(x) ? `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(2)}R` : "–");

  function unreliable(q) { return !!(q && q.check && q.check.reliable === false); }
  // the word shown for a grade: switched off ("Ranked #n") when the track record shows the grades aren't working
  function gradeWord(grade, q, rank) { return unreliable(q) ? `Ranked #${rank}` : grade; }
  function gradeClassFor(grade, q) {
    if (unreliable(q)) return "calm";
    return grade === "Strong" ? "good" : grade === "Moderate" ? "calm" : grade === "Weak" ? "warn" : "bad";
  }
  // banner for the ideas list
  function ideasBanner(q) {
    if (!unreliable(q)) return "";
    return `<div class="banner warn"><b>The grades aren't reliable right now, so they're switched off.</b> ${esc(q.check.reason)} Ideas are still listed in score order, but words like "Strong" are hidden until higher grades prove they do better. See the Track record tab.</div>`;
  }
  // table for the Track record tab
  function gradeTable(q, title = "How each grade actually turned out") {
    if (!q || !q.by_grade || !q.by_grade.length) return `<h3>${esc(title)}</h3><p class="small muted">No finished ideas yet.</p>`;
    const c = q.check || {};
    const verdict = c.reliable === true ? `<div class="banner good"><b>The grades are working:</b> ${esc(c.reason)}</div>`
      : c.reliable === false ? `<div class="banner bad"><b>The grades are not working right now:</b> ${esc(c.reason)} Grade words are switched off on the ideas until this changes.</div>`
        : `<div class="banner calm"><b>Not proven yet:</b> ${esc(c.reason || "")}</div>`;
    const table = (rowsIn) => `<div class="table-wrap"><table><thead><tr><th>Grade given</th><th class="num">Ideas</th><th class="num">Ended in profit</th><th class="num">Average after fees</th><th class="num">Per $1 risked</th><th class="num">Random pick</th></tr></thead><tbody>${rowsIn.map((g) => `<tr><td>${esc(g.grade)}</td><td class="num">${g.ideas}</td><td class="num">${Math.round(g.win_rate * 100)}%</td>
      <td class="num ${g.avg_return >= 0 ? "up" : "down"}">${pct(g.avg_return)}</td><td class="num ${g.avg_r >= 0 ? "up" : "down"}">${R(g.avg_r)}</td>
      <td class="num">${pct(g.random_avg_return)}</td></tr>`).join("")}</tbody></table></div>`;
    const lite = (ck) => (ck.reliable === true ? `<span class="pill good">grades working here</span>` : ck.reliable === false ? `<span class="pill bad">grades not working here</span>` : `<span class="pill calm">not proven yet</span>`);
    const groups = (q.groups || []).filter((g) => g.by_grade && g.by_grade.length);
    const body = groups.length > 1
      ? groups.map((g) => `<h4>${esc(g.name)} ${lite(g.check || {})}</h4>${table(g.by_grade)}`).join("") + `<h4>All markets together</h4>${table(q.by_grade)}`
      : groups.length === 1 ? `<h4>${esc(groups[0].name)}</h4>${table(q.by_grade)}` : table(q.by_grade);
    const v = q.vol || {};
    const volNote = v.n >= 30 && v.slope ? `<p class="small muted">Jumpiness adjustment (learned from ${v.n} finished ideas): ${v.slope < 0 ? `jumpier coins did worse than their scores said, so their expected results are lowered by about ${Math.abs(v.slope).toFixed(2)}R per step of extra jumpiness` : `jumpier coins did slightly better than their scores said, so they get a small boost of ${v.slope.toFixed(2)}R per step`}.</p>`
      : `<p class="small muted">The jumpiness adjustment starts once 30 ideas have finished.</p>`;
    return `<h3>${esc(title)}</h3>${verdict}${body}
      <p class="small muted">"Per $1 risked" (R) divides each result by the distance to its safety exit, so a jumpy coin's big swings and a calm coin's small ones are compared fairly. Higher grades should show higher numbers here; when they don't, the grades are switched off.</p>${volNote}`;
  }
  root.OmegaQuality = { unreliable, gradeWord, gradeClassFor, ideasBanner, gradeTable };
})(typeof window !== "undefined" ? window : globalThis);
