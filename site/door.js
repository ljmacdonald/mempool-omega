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

/* Best idea right now (DECISIONS D117): the one idea from a proven part of the product, or "sitting in cash". */
(() => {
  "use strict";
  const box = document.getElementById("best");
  const G = window.OMEGA_GATE;
  if (!box || !G) return;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (x) => (Number.isFinite(+x) && x !== null ? (+x >= 100 ? (+x).toLocaleString(undefined, { maximumFractionDigits: 2 }) : (+x).toPrecision(4)) : "–");
  const pct = (x) => (Number.isFinite(x) ? `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(1)}%` : "–");
  const ago = (t) => { const m = Math.round((Date.now() - Date.parse(t)) / 60000); return !Number.isFinite(m) ? "" : m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`; };
  G.ready.then(() => {
    const b = G.best;
    if (!b) { box.innerHTML = '<span class="bc-k">Best idea right now</span><p class="muted">Couldn\'t load right now: try again in a minute.</p>'; return; }
    const proven = (b.proven_units || []).length;
    const m = G.market && G.market.now;
    const mkt = m ? `<span class="pill ${m.falling ? "warn" : "good"}">Market ${m.falling ? "falling" : "rising"}</span>` : "";
    const i = b.idea;
    if (i) {
      const r = i.record || {};
      box.className = "best-card has-idea";
      box.innerHTML = `<div class="bc-top"><span class="bc-k">Best idea right now</span>${mkt}<span class="muted small">updated ${esc(ago(b.generated_at))}</span></div>
        <h2 class="bc-title">${esc(i.title)}</h2>
        <div class="bc-facts"><div><span>Price</span><b>${num(i.price)}</b></div><div><span>Safety exit</span><b>${num(i.stop)}</b></div><div><span>Target</span><b>${i.target ? num(i.target) : "see rule"}</b></div></div>
        <p class="bc-exit">${esc(i.exit || "")}</p>
        <p class="bc-rec">How this kind of idea has done: <b>${r.hit != null ? `won ${Math.round(r.hit * 100)}%` : ""}${r.n ? ` of ${(+r.n).toLocaleString()} trades` : ""}</b>${r.avg != null ? `, average <b>${pct(r.avg)}</b> after costs` : ""} (${esc(r.name || "")}, ${esc(r.source || "")}). Past results don't guarantee the next trade.</p>
        <a class="btn primary" href="${esc(i.path)}">Open it on its page →</a> <span class="muted small">Paper-tested only. Not financial advice.</span>`;
      return;
    }
    const wait = (b.waiting || [])[0];
    const soon = (b.soon || [])[0];
    box.className = "best-card no-idea";
    box.innerHTML = `<div class="bc-top"><span class="bc-k">Best idea right now</span>${mkt}<span class="muted small">checked ${esc(ago(b.generated_at))}</span></div>
      <h2 class="bc-title">${wait ? "A proven idea is waiting: the market is falling" : "No idea passes our tests right now. Sitting in cash."}</h2>
      <p>${wait ? `${esc(wait.title)} is from a proven strategy, but crypto buy ideas are paused while Bitcoin is below its 7-day average.`
        : `We checked <b>${b.considered}</b> live ideas; none came from the <b>${proven}</b> part${proven === 1 ? "" : "s"} of the product that ${proven === 1 ? "has" : "have"} passed our honest tests${proven ? ` (${esc(b.proven_units.join("; "))})` : ""}. Doing nothing is often the best trade, and we'd rather say so than show you a weak one.`}</p>
      ${soon ? `<p class="bc-soon"><b>Coming up:</b> ${esc(soon.title)}. ${esc(soon.text)} <a href="${esc(soon.path)}">Open →</a></p>` : ""}
      <a href="results/#proof">What "passed our honest tests" means →</a>`;
  });
})();
