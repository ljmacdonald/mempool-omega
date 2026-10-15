/* The front door (DECISIONS D99): "Is it working?" and "Watch it trade" filled in from the live summary
   (infra/summary.py). Plain words, no claims: it repeats what the Results page shows. */
(() => {
  "use strict";
  const DATA = window.OMEGA_REPO || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/main/state/";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  fetch(`${DATA}summary.json?t=${Math.floor(Date.now() / 300000)}`).then((r) => (r.ok ? r.json() : null)).then((S) => {
    if (!S) return;
    const secs = S.sections || [];
    const rest = secs.filter((s) => !s.key.startsWith("challenge"));
    const by = (k) => rest.filter((s) => s.status === k);
    const ahead = by("ahead").map((s) => s.name);
    const w = document.getElementById("doorWorking");
    if (w) w.innerHTML = `${ahead.length ? `<b>${esc(ahead.join(", "))}</b> ${ahead.length === 1 ? "is" : "are"} ahead of random picks.` : "Nothing is clearly ahead of random picks yet."}
      ${by("behind").length} behind, ${by("early").length} too early to tell. See every record →`;
    const ch = secs.filter((s) => s.key.startsWith("challenge") && typeof s.equity === "number");
    const t = document.getElementById("doorTrade");
    if (t && ch.length) t.innerHTML = `${ch.map((s) => `${esc(s.name.replace("$100 ", ""))}: <b>$${s.equity.toFixed(2)}</b>`).join(" · ")} from $100 each, every step written down →`;
  }).catch(() => { /* the static text stays */ });
})();
