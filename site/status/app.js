/* System status (DECISIONS D98): the watchdog's view of every page and background job, from infra/summary.py. */
(() => {
  "use strict";
  const DATA = window.OMEGA_REPO || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/main/state/";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fin = (x) => typeof x === "number" && Number.isFinite(x);
  const ago = (m) => (!fin(m) ? "never" : m < 60 ? `${m} min ago` : m < 48 * 60 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`);
  const within = (m) => (m < 60 ? `${m} min` : `${Math.round(m / 60)} h`);
  const BASE = new URL("../", location.href).href;

  function render(S) {
    const age = Math.round((Date.now() - Date.parse(S.generated_at)) / 60000);
    const late = age > 45;
    $("summary").className = `banner ${S.ok && !late ? "good" : "warn"}`;
    $("summary").innerHTML = late ? `<b>The watchdog itself last ran ${esc(ago(age))}.</b> It normally runs every 15 minutes, so this view may be out of date.`
      : S.ok ? "<b>All systems normal.</b> Every page is up to date and every background job is running."
        : `<b>${S.problems.length} problem${S.problems.length === 1 ? "" : "s"} found.</b> The affected pages show a warning and the owner has been notified.<ul>${S.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>`;
    $("fresh").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Page</th><th>Last update</th><th>Expected within</th><th>State</th></tr></thead><tbody>
      ${(S.sections || []).map((s) => `<tr><td><a href="${BASE}${esc(s.path)}">${esc(s.name)}</a></td><td>${esc(ago(s.age_min))}</td><td>${esc(within(s.max_age_min))}</td>
        <td>${s.stale ? '<span class="pill bad">out of date</span>' : s.market_closed ? '<span class="pill calm">market closed</span>' : '<span class="pill good">up to date</span>'}</td></tr>`).join("")}
      </tbody></table></div>`;
    const jobs = S.failing_jobs || [];
    $("jobs").innerHTML = !jobs.length ? `<p><span class="pill good">OK</span> No background job has failed twice in a row.</p>`
      : `<ul>${jobs.map((j) => `<li><b>${esc(j.workflow)}</b> failed its last two runs (since ${esc(new Date(j.since).toLocaleString())}).</li>`).join("")}</ul>`;
    const st = document.querySelector(".top .status") || $("status");
    if (st) st.textContent = `Checked ${ago(age)}`;
  }

  async function load() {
    try {
      const r = await fetch(`${DATA}summary.json?t=${Date.now()}`, { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      render(await r.json());
    } catch {
      $("summary").className = "banner warn";
      $("summary").textContent = "The status check isn't available right now. Try again in a few minutes.";
    }
  }
  load(); setInterval(load, 2 * 60e3);
})();
