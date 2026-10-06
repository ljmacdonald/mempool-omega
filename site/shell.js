/* The app shell shared by every page: turns each page's header into the app bar (brand, page links grouped by
   market, alerts, colours, help) and a page header (section, name, purpose, live status), and adds the onboarding:
   a short welcome tour on the first visit, a "Getting started" checklist that ticks itself off as the visitor uses
   the product, "continue where you left off" and a reminder of tracked trades. Purely presentational: it moves the
   page's own elements (same ids, same listeners) and reads, never changes, the trading data in this browser. */
(function (root) {
  "use strict";
  const SCRIPT = document.currentScript;
  const BASE = new URL(".", SCRIPT ? SCRIPT.src : location.href).href;
  const PAGES = {
    main: { path: "", name: "Exchange coins", group: "Crypto", blurb: "The best-scoring coins on major exchanges right now, re-ranked every few minutes." },
    small: { path: "small/", name: "Small coins", group: "Crypto", blurb: "Smaller, faster-moving coins: bigger swings both ways, scored the same honest way." },
    dex: { path: "dex/", name: "DEX tokens", group: "Crypto", blurb: "Tokens on decentralised exchanges, scam-checked before they're ever suggested." },
    listings: { path: "listings/", name: "New listings", group: "Crypto", blurb: "Coins just listed on big exchanges, and what really happens after a listing." },
    sniper: { path: "sniper/", name: "Sniper lab", group: "Crypto", blurb: "Brand-new DEX tokens, scam-checked and paper-sniped: does it pay after the bots?" },
    stocks: { path: "stocks/", name: "US stocks", group: "Stocks & FX", blurb: "The most-traded US stocks and a high-volatility list, during market hours." },
    fx: { path: "fx/", name: "Forex", group: "Stocks & FX", blurb: "Currency pairs, gold and silver, buy or sell, checked for manipulation." },
    ict: { path: "ict/", name: "ICT setups", group: "Research", blurb: "Inner Circle Trader setups coded exactly, with how they really did." },
    lab: { path: "lab/", name: "Strategy lab", group: "Research", blurb: "Famous public strategies re-tested honestly, and which ones still hold up." },
    whales: { path: "whales/", name: "Whale tracker", group: "Research", blurb: "Last month's best big traders, what they hold now, and a paper copy of them." },
  };
  const GROUPS = ["Crypto", "Stocks & FX", "Research"];
  const TRADE_KEYS = { "omega.trades": "main", "omega.dex.trades": "dex", "omega.stk.trades": "stocks", "omega.fx.trades": "fx", "omega.ict.trades": "ict" };
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode: onboarding just repeats */ } },
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const href = (k) => BASE + PAGES[k].path;

  function currentPage() {
    if (root.OMEGA_MODE === "small") return "small";
    const first = location.href.replace(/[?#].*$/, "").replace(BASE, "").split("/")[0];
    return PAGES[first] && first !== "main" ? first : "main";
  }
  const HERE = currentPage();

  // ------------------------------------------------------------------ app bar + page header
  const logo = '<span class="brand-mark" aria-hidden="true">Ω</span>';
  function buildShell() {
    const header = document.querySelector("header.top");
    if (!header || header.classList.contains("appbar")) return;
    const nav = header.querySelector("nav.sites");
    const right = header.querySelector(".top-right");
    const h1 = header.querySelector("h1");
    const sub = header.querySelector("p");
    const meta = [...header.querySelectorAll(".top-right > .tag, .top-right > .status")];
    // grouped page links (the same <a> elements, moved)
    if (nav) {
      const links = Object.fromEntries([...nav.querySelectorAll("a[data-site]")].map((a) => [a.dataset.site, a]));
      nav.innerHTML = "";
      for (const g of GROUPS) {
        const box = document.createElement("span"); box.className = "nav-group";
        box.innerHTML = `<span class="nav-label">${esc(g)}</span>`;
        for (const [k, p] of Object.entries(PAGES)) if (p.group === g && links[k]) box.appendChild(links[k]);
        if (box.children.length > 1) nav.appendChild(box);
      }
    }
    const help = document.createElement("button");
    help.type = "button"; help.className = "icon-btn"; help.title = "How this works (tour)"; help.setAttribute("aria-label", "How this works");
    help.textContent = "?"; help.addEventListener("click", () => tour(true));
    if (right) right.appendChild(help);
    // the app bar
    const inner = document.createElement("div"); inner.className = "appbar-in";
    const brand = document.createElement("a"); brand.className = "brand"; brand.href = BASE;
    brand.innerHTML = `${logo}<span>Mempool Omega<small>Tested trade ideas</small></span>`;
    inner.appendChild(brand);
    if (nav) inner.appendChild(nav);
    if (right) inner.appendChild(right);
    // the page header, under the bar
    const hero = document.createElement("section"); hero.className = "page-hero";
    const left = document.createElement("div");
    const p = PAGES[HERE];
    left.innerHTML = `<div class="eyebrow">${esc(p.group)}</div>`;
    if (h1) { left.appendChild(h1); tidyTitle(h1); new MutationObserver(() => tidyTitle(h1)).observe(h1, { childList: true, characterData: true, subtree: true }); }
    if (sub) { left.appendChild(sub); tidySub(sub); new MutationObserver(() => tidySub(sub)).observe(sub, { childList: true, characterData: true, subtree: true }); }
    const metaBox = document.createElement("div"); metaBox.className = "hero-meta";
    for (const m of meta) metaBox.appendChild(m);
    hero.append(left, metaBox);
    header.innerHTML = ""; header.appendChild(inner); header.classList.add("appbar");
    // sticky control rails dock right under the bar, whatever its height on this screen
    const fit = () => document.documentElement.style.setProperty("--appbar-h", `${Math.ceil(header.getBoundingClientRect().height)}px`);
    fit();
    if ("ResizeObserver" in root) new ResizeObserver(fit).observe(header); else root.addEventListener("resize", fit);
    header.after(hero);
    resumeChips(metaBox);
    footer();
  }
  // "Mempool Omega · Forex" -> "Forex" (the brand is in the bar); the main page's plain "Mempool Omega" -> its page name
  function tidyTitle(h1) {
    const t = h1.textContent.trim();
    const want = t.includes("·") ? t.split("·").slice(1).join("·").trim() : t === "Mempool Omega" ? PAGES[HERE].name : t;
    if (want !== t) h1.textContent = want;
  }
  // keep the page's own description; the "not financial advice" line moves to the footer
  function tidySub(p) {
    const t = p.textContent;
    const want = t.replace(/\s*·\s*practice and education only, not financial advice\s*$/i, "");
    if (want !== t) p.textContent = want;
  }
  function footer() {
    const f = document.querySelector("footer");
    if (!f || f.querySelector(".foot-brand")) return;
    const b = document.createElement("div"); b.className = "foot-brand";
    b.innerHTML = `${logo}<span>Mempool Omega · paper trading and education only, not financial advice</span>`;
    f.prepend(b);
  }

  // ------------------------------------------------------------------ coming back: where you were, what you're tracking
  function trades() {
    const out = {};
    for (const [k, page] of Object.entries(TRADE_KEYS)) { const t = store.get(k, []); if (Array.isArray(t) && t.length) out[page] = (out[page] || 0) + t.length; }
    return out;
  }
  function resumeChips(box) {
    const last = store.get("omega.shell.last", null);
    const visited = store.get("omega.shell.visited", {});
    visited[HERE] = Date.now(); store.set("omega.shell.visited", visited);
    store.set("omega.shell.last", { page: HERE, at: Date.now() });
    const t = trades(); const total = Object.values(t).reduce((a, b) => a + b, 0);
    if (total) {
      const page = t[HERE] ? HERE : Object.keys(t)[0];
      const a = document.createElement("a"); a.className = "chip"; a.href = `${href(page)}#/trades`;
      a.textContent = `● Tracking ${total} paper trade${total === 1 ? "" : "s"}`;
      if (page === HERE) a.addEventListener("click", (e) => { const b = document.querySelector('nav.tabs button[data-tab="trades"]'); if (b) { e.preventDefault(); b.click(); b.scrollIntoView({ block: "center" }); } });
      box.prepend(a);
    }
    if (last && last.page !== HERE && PAGES[last.page] && Date.now() - last.at < 7 * 86400e3 && HERE === "main") {
      const a = document.createElement("a"); a.className = "chip quiet"; a.href = href(last.page);
      a.textContent = `Continue: ${PAGES[last.page].name} →`;
      box.prepend(a);
    }
  }

  // ------------------------------------------------------------------ welcome tour
  const STEPS = () => [
    { icon: "Ω", title: "Welcome to Mempool Omega", html: `<p>Trade ideas for crypto, US stocks and forex, scored by models that are <b>tested honestly</b>: after fees, against random picks, and on data they never saw.</p><ul><li>Every idea is tracked to its end, wins and losses, so you can see what really works.</li><li>It's <b>paper trading only</b>: nothing here touches your money.</li></ul>` },
    { icon: "📊", title: "How to read an idea", html: `<ul><li><b>Score out of 10</b> and a <b>grade</b> (Strong, Moderate, Weak, Avoid): above 5 means better than break-even after costs.</li><li><b>Take profit</b> and <b>safety exit</b>: where to sell for a gain, and where to get out before a small loss becomes a big one.</li><li><b>Sell by</b>: every idea has a time limit.</li><li>Warnings appear in amber. If the grades stop being reliable, they switch themselves off.</li></ul>` },
    { icon: "🔔", title: "Make it yours", html: `<ul><li>Tap <b>“I bought this”</b> on an idea to follow it in <b>My trades</b>: the page tells you when to take profit or get out.</li><li>Turn on <b>Alerts</b> (top right) for pop-ups with sound when a strong idea appears or a trade needs action.</li><li>Everything stays in this browser. No account, no sign-up.</li></ul>`, cta: true },
  ];
  function tour(force) {
    if (!force && store.get("omega.onb.tour", false)) return;
    const steps = STEPS(); let i = 0;
    const bd = document.createElement("div"); bd.className = "ob-backdrop"; bd.setAttribute("role", "dialog"); bd.setAttribute("aria-modal", "true"); bd.setAttribute("aria-label", "Welcome");
    const close = (done) => { bd.remove(); document.removeEventListener("keydown", onKey); if (done) { store.set("omega.onb.tour", true); checklist(); } };
    const onKey = (e) => { if (e.key === "Escape") close(true); };
    function paint() {
      const s = steps[i]; const lastStep = i === steps.length - 1;
      bd.innerHTML = `<div class="ob-modal"><div class="ob-art"><span class="ob-icon">${s.icon}</span></div>
        <div class="ob-body"><h2>${esc(s.title)}</h2>${s.html}</div>
        <div class="ob-foot"><div class="ob-dots">${steps.map((_, k) => `<i class="${k === i ? "on" : ""}"></i>`).join("")}</div>
          <div class="actions">${lastStep ? "" : '<button type="button" class="ob-skip" data-skip>Skip</button>'}
            ${lastStep && s.cta ? '<button type="button" class="btn" data-alerts>Turn on alerts</button>' : ""}
            <button type="button" class="btn primary" data-next>${lastStep ? "Start exploring" : "Next"}</button></div></div></div>`;
      bd.querySelector("[data-next]").focus();
    }
    bd.addEventListener("click", (e) => {
      if (e.target === bd || e.target.closest("[data-skip]")) close(true);
      else if (e.target.closest("[data-next]")) { if (i < steps.length - 1) { i += 1; paint(); } else close(true); }
      else if (e.target.closest("[data-alerts]")) { close(true); setTimeout(() => { const b = document.querySelector(".oa-bell"); if (b) b.click(); }, 50); }
    });
    document.addEventListener("keydown", onKey);
    paint(); document.body.appendChild(bd);
  }

  // ------------------------------------------------------------------ getting-started checklist
  function items() {
    const visited = Object.keys(store.get("omega.shell.visited", {}));
    const alertsOn = !!(store.get("omega.alerts.cfg", {}) || {}).on;
    return [
      { id: "tour", done: !!store.get("omega.onb.tour", false), t: "Take the 1-minute tour", d: "How scores, grades and exits work.", go: () => tour(true) },
      { id: "pages", done: visited.length >= 2, t: "Look at two different markets", d: "Crypto, stocks, forex: pick from the bar at the top.", go: () => { location.href = href(HERE === "main" ? "stocks" : "main"); } },
      { id: "trade", done: Object.keys(trades()).length > 0, t: "Follow a paper trade", d: "Tap “I bought this” on an idea, then watch My trades.", go: () => { const b = document.querySelector('nav.tabs button[data-tab="ideas"], nav.tabs button[data-tab="setups"]'); if (b) { b.click(); b.scrollIntoView({ block: "center" }); } else location.href = href("main"); } },
      { id: "alerts", done: alertsOn, t: "Turn on alerts", d: "Pop-ups with sound when it matters.", go: () => { const b = document.querySelector(".oa-bell"); if (b) b.click(); } },
    ];
  }
  function checklist() {
    const st = store.get("omega.onb.gs", { hidden: false, open: false });
    let box = document.querySelector(".gs");
    const list = items(); const done = list.filter((x) => x.done).length;
    if (st.hidden || done === list.length || !store.get("omega.onb.tour", false)) {
      if (box) box.remove();
      if (done === list.length && !st.celebrated) { store.set("omega.onb.gs", { ...st, celebrated: true, hidden: true }); }
      return;
    }
    const sig = JSON.stringify([st.open, list.map((x) => x.done)]);
    if (box && box.dataset.sig === sig) return;                   // nothing changed: don't redraw under the visitor's finger
    if (!box) { box = document.createElement("div"); box.className = "gs"; document.body.appendChild(box); }
    box.dataset.sig = sig;
    const pct = Math.round((done / list.length) * 100);
    box.innerHTML = `${st.open ? `<div class="gs-panel" role="region" aria-label="Getting started">
        <header><b>Getting started</b><button class="gs-x" type="button" data-hide title="Hide">×</button></header>
        <div class="gs-bar"><i style="width:${pct}%"></i></div>
        <ul class="gs-list">${list.map((x) => `<li class="${x.done ? "done" : ""}"><span class="tick">${x.done ? "✓" : ""}</span><span>${x.done ? `<span class="t">${esc(x.t)}</span>` : `<a href="#" data-go="${x.id}" class="t">${esc(x.t)}</a>`}<span class="d">${esc(x.d)}</span></span></li>`).join("")}</ul>
      </div>` : ""}
      <button class="gs-pill" type="button" data-toggle aria-expanded="${st.open}"><span class="gs-ring" style="--p:${pct}"><span>${done}/${list.length}</span></span>Getting started</button>`;
    box.onclick = (e) => {
      const go = e.target.closest("[data-go]");
      if (go) { e.preventDefault(); const it = list.find((x) => x.id === go.dataset.go); if (it) it.go(); return; }
      if (e.target.closest("[data-hide]")) { store.set("omega.onb.gs", { ...st, hidden: true }); box.remove(); return; }
      if (e.target.closest("[data-toggle]")) { store.set("omega.onb.gs", { ...st, open: !st.open }); checklist(); }
    };
  }

  function start() {
    buildShell();
    if (!store.get("omega.onb.tour", false)) setTimeout(() => tour(false), 700);
    else checklist();
    root.addEventListener("storage", checklist);
    setInterval(checklist, 4000);           // ticks items off as the visitor uses the page (trades, alerts)
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
  root.OmegaShell = { tour, page: HERE };
})(window);
