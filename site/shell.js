/* The app shell shared by every page: turns each page's header into the app bar (brand, page links grouped by
   market, alerts, colours, help) and a page header (section, name, purpose, live status), and adds the onboarding:
   a short welcome tour on the first visit, a "Getting started" checklist that ticks itself off as the visitor uses
   the product, "continue where you left off" and a reminder of tracked trades. Purely presentational: it moves the
   page's own elements (same ids, same listeners) and reads, never changes, the trading data in this browser. */
(function (root) {
  "use strict";
  const SCRIPT = document.currentScript;
  const BASE = new URL(".", SCRIPT ? SCRIPT.src : location.href).href;
  // risk: how hard the prices swing; kind: what the visitor gets (ideas to act on, or research to watch)
  const PAGES = {
    main: { path: "coins/", name: "Exchange coins", group: "crypto", risk: "Medium risk", kind: "Live ideas", blurb: "The best-scoring coins on big exchanges like Binance, re-ranked every few minutes.", action: "Pick a speed, read the top idea, and only act on Strong or Moderate grades." },
    small: { path: "small/", name: "Small coins", group: "crypto", risk: "High risk", kind: "Live ideas", blurb: "Smaller, faster-moving coins: bigger swings both ways, scored the same honest way.", action: "Use smaller amounts than usual: these swing hard." },
    dex: { path: "dex/", name: "DEX tokens", group: "crypto", risk: "High risk", kind: "Live ideas", blurb: "Tokens on decentralised exchanges, scam-checked before they're ever suggested.", action: "Keep the safety level on Standard and copy the real token address from the card." },
    listings: { path: "listings/", name: "New listings", group: "new", risk: "Very high risk", kind: "Tested rules", blurb: "Coins just listed on big exchanges, and what really happens after a listing.", action: "Only act on the tested ideas at the top; most new listings fall." },
    sniper: { path: "sniper/", name: "Sniper lab", group: "new", risk: "Paper only", kind: "Research", blurb: "Brand-new DEX tokens, scam-checked and paper-sniped: does it pay after the bots?", action: "Watch only: no real-money sniping until the verdict says it holds up." },
    stocks: { path: "stocks/", name: "US stocks", group: "markets", risk: "Medium risk", kind: "Live ideas", blurb: "The most-traded US stocks and a high-volatility list, during market hours.", action: "Start with Large stocks; ideas appear 9:30–16:00 New York time." },
    fx: { path: "fx/", name: "Forex & gold", group: "markets", risk: "Medium risk", kind: "Live ideas", blurb: "Currency pairs, gold and silver, buy or sell, checked for manipulation.", action: "Start with the major pairs and avoid the news windows." },
    ict: { path: "ict/", name: "ICT setups", group: "strategies", risk: "Medium risk", kind: "Live setups", blurb: "Inner Circle Trader setups coded exactly, with exact entries and how they really did.", action: "Place the limit order only when the grade and test evidence are good." },
    lab: { path: "lab/", name: "Strategy lab", group: "strategies", risk: "Risk varies", kind: "Tested ideas", blurb: "Famous public strategies re-tested honestly, and which ones still hold up.", action: "Check the Scoreboard: only strategies marked Held up give ideas." },
    whales: { path: "whales/", name: "Whale tracker", group: "strategies", risk: "Paper only", kind: "Research", blurb: "Last month's best big traders, what they hold now, and a paper copy of them.", action: "Information only until the 30-day verdict is in." },
  };
  // the menu: what the visitor wants to trade, then the strategies and research that cut across markets
  const GROUPS = [
    { id: "crypto", name: "Crypto", blurb: "Coins and tokens, scored live" },
    { id: "new", name: "New coins", blurb: "Fresh listings and launches" },
    { id: "markets", name: "Stocks & forex", blurb: "US shares, currencies, gold" },
    { id: "strategies", name: "Strategies", blurb: "Proven methods and research" },
  ];
  const GROUP = Object.fromEntries(GROUPS.map((g) => [g.id, g]));
  const TRADE_KEYS = { "omega.trades": "main", "omega.dex.trades": "dex", "omega.stk.trades": "stocks", "omega.fx.trades": "fx", "omega.ict.trades": "ict" };
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode: onboarding just repeats */ } },
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const href = (k) => BASE + PAGES[k].path;

  function currentPage() {
    if (root.OMEGA_MODE === "small") return "small";
    if (root.OMEGA_MODE === "home") return "home";              // the Ask Omega front page (site/index.html)
    const first = location.href.replace(/[?#].*$/, "").replace(BASE, "").split("/")[0];
    return PAGES[first] && first !== "main" ? first : "main";   // site/coins/ is Exchange coins ("main")
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
    // grouped page menus (the same <a> elements, moved, each with what the page is for)
    if (nav) buildMenus(nav);
    const help = document.createElement("button");
    help.type = "button"; help.className = "icon-btn xp-help"; help.title = "Help: explain this page, glossary, tour"; help.setAttribute("aria-label", "Help");
    help.setAttribute("aria-haspopup", "menu");
    help.textContent = "?"; help.addEventListener("click", () => (root.OmegaExplain ? root.OmegaExplain.helpMenu(help) : tour(true)));
    if (right) right.appendChild(help);
    // the app bar
    const inner = document.createElement("div"); inner.className = "appbar-in";
    const brand = document.createElement("a"); brand.className = "brand"; brand.href = BASE;
    brand.innerHTML = `${logo}<span>Mempool Omega<small>Tested trade ideas</small></span>`;
    inner.appendChild(brand);
    if (nav) {
      const menu = document.createElement("button"); menu.type = "button"; menu.className = "nav-burger"; menu.setAttribute("aria-label", "All pages"); menu.setAttribute("aria-expanded", "false");
      menu.innerHTML = '<span aria-hidden="true">☰</span> Menu';
      // on phones the menus open as a full-screen sheet (outside the bar, whose blur would clip it)
      menu.addEventListener("click", () => {
        let sheet = document.querySelector(".nav-sheet");
        const open = !sheet;
        if (open) {
          sheet = document.createElement("nav"); sheet.className = "nav-sheet"; sheet.setAttribute("aria-label", "All pages");
          for (const dd of nav.querySelectorAll(".nav-dd")) { const c = dd.cloneNode(true); c.classList.remove("open"); const t = c.querySelector(".nav-trigger"); t.replaceWith(Object.assign(document.createElement("div"), { className: "nav-sheet-h", textContent: t.textContent })); sheet.appendChild(c); }
          sheet.style.top = `${Math.ceil(header.getBoundingClientRect().bottom)}px`;
          document.body.appendChild(sheet);
        } else sheet.remove();
        menu.setAttribute("aria-expanded", String(open)); document.documentElement.classList.toggle("nav-open", open);
      });
      inner.append(menu, nav);
    }
    if (right) inner.appendChild(right);
    // the page header, under the bar (the front page has its own: the chat)
    const p = PAGES[HERE];
    if (!p) {
      header.innerHTML = ""; header.appendChild(inner); header.classList.add("appbar");
      const fitHome = () => document.documentElement.style.setProperty("--appbar-h", `${Math.ceil(header.getBoundingClientRect().height)}px`);
      fitHome(); if ("ResizeObserver" in root) new ResizeObserver(fitHome).observe(header);
      visitHome(); footer();
      return;
    }
    const hero = document.createElement("section"); hero.className = "page-hero";
    const left = document.createElement("div");
    left.innerHTML = `<div class="eyebrow">${esc(GROUP[p.group].name)}<span class="crumb-sep" aria-hidden="true">›</span>${esc(p.name)}<span class="hero-kind">${esc(p.kind)}</span><span class="hero-kind">${esc(p.risk)}</span></div>`;
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
    tidyTabs();
    sectionBar();
  }

  function buildMenus(nav) {
    const links = Object.fromEntries([...nav.querySelectorAll("a[data-site]")].map((a) => [a.dataset.site, a]));
    nav.innerHTML = "";
    nav.setAttribute("aria-label", "All pages");
    for (const g of GROUPS) {
      const keys = Object.keys(PAGES).filter((k) => PAGES[k].group === g.id && links[k]);
      if (!keys.length) continue;
      const dd = document.createElement("div"); dd.className = "nav-dd"; if (keys.includes(HERE)) dd.classList.add("current");
      const id = `nav-${g.id}`;
      dd.innerHTML = `<button type="button" class="nav-trigger" aria-expanded="false" aria-controls="${id}">${esc(g.name)}<span class="caret" aria-hidden="true"></span></button><div class="nav-menu" id="${id}"><div class="nav-menu-h"><b>${esc(g.name)}</b><span>${esc(g.blurb)}</span></div></div>`;
      const menu = dd.querySelector(".nav-menu");
      for (const k of keys) {
        const a = links[k]; const p = PAGES[k];
        a.className = "nav-item";
        a.innerHTML = `<span class="ni-name">${esc(p.name)}${k === HERE ? '<span class="ni-here">You are here</span>' : ""}</span><span class="ni-blurb">${esc(p.blurb)}</span><span class="ni-tags"><span>${esc(p.kind)}</span><span class="${/Very high|High/.test(p.risk) ? "hot" : /Paper/.test(p.risk) ? "paper" : ""}">${esc(p.risk)}</span></span>`;
        menu.appendChild(a);
      }
      const btn = dd.querySelector(".nav-trigger");
      btn.addEventListener("click", (e) => { e.stopPropagation(); const open = !dd.classList.contains("open"); closeMenus(); dd.classList.toggle("open", open); btn.setAttribute("aria-expanded", String(open)); });
      dd.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeMenus(); btn.focus(); } });
      nav.appendChild(dd);
    }
    document.addEventListener("click", (e) => { if (!e.target.closest(".nav-dd")) closeMenus(); });
  }
  function closeMenus() { for (const d of document.querySelectorAll(".nav-dd.open")) { d.classList.remove("open"); d.querySelector(".nav-trigger").setAttribute("aria-expanded", "false"); } }

  // the same sections, in the same order, with the same names on every page
  const TAB_ORDER = ["ideas", "setups", "trades", "movers", "news", "record", "improve", "account", "guide"];
  const TAB_NAME = { trades: "My trades", guide: "How it works" };
  function tidyTabs() {
    const bar = document.querySelector("nav.tabs"); if (!bar) return;
    const bs = [...bar.querySelectorAll("button[data-tab]")];
    bs.sort((a, b) => TAB_ORDER.indexOf(a.dataset.tab) - TAB_ORDER.indexOf(b.dataset.tab)).forEach((b) => bar.appendChild(b));
    for (const b of bs) {
      const t = b.firstChild && b.firstChild.nodeType === 3 ? b.firstChild : null;
      const want = TAB_NAME[b.dataset.tab] || (b.dataset.tab === "ideas" && t ? t.textContent.replace(" DEX ideas", " ideas") : null);
      if (t && want) t.textContent = b.querySelector(".count") ? `${want.trim()} ` : want.trim();
    }
  }
  // pages without tabs get an "on this page" bar that jumps to each section
  function sectionBar() {
    if (document.querySelector("nav.tabs")) return;
    const cards = [...document.querySelectorAll(".wrap > article.card, .wrap > section.card")].filter((c) => c.querySelector(":scope > h2"));
    if (cards.length < 3) return;
    const bar = document.createElement("nav"); bar.className = "tabs sections"; bar.setAttribute("aria-label", "On this page");
    cards.forEach((c, i) => {
      if (!c.id) c.id = `sec-${i + 1}`;
      const t = c.querySelector(":scope > h2").textContent.replace(/[:,(].*$/, "").trim();
      const a = document.createElement("a"); a.href = `#${c.id}`; a.textContent = t.length > 34 ? `${t.slice(0, 32)}…` : t;
      bar.appendChild(a);
    });
    cards[0].before(bar);
    const links = [...bar.querySelectorAll("a")];
    if ("IntersectionObserver" in root) {
      const io = new IntersectionObserver((es) => { for (const e of es) if (e.isIntersecting) for (const a of links) a.classList.toggle("on", a.getAttribute("href") === `#${e.target.id}`); }, { rootMargin: "-30% 0px -60% 0px" });
      cards.forEach((c) => io.observe(c));
    }
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
  function visitHome() { const v = store.get("omega.shell.visited", {}); v.home = Date.now(); store.set("omega.shell.visited", v); }
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
    { icon: "💡", title: "Never guess what something means", html: `<ul><li>Any label with a <span class="xp-l">dotted underline</span> explains itself: click it to see what it means and <b>what to do</b>.</li><li>A small <span class="xp-i">i</span> next to a button or setting does the same.</li><li>The <b>?</b> at the top right has <b>Explain mode</b> (tap anything to learn what it does) and a <b>glossary</b> of every term.</li><li>Each page starts with three steps: how to use it.</li></ul>` },
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
      { id: "pages", done: visited.filter((k) => k !== "home").length >= 2, t: "Look at two different markets", d: "Crypto, stocks, forex: pick from the menus at the top.", go: () => { location.href = href(HERE === "main" ? "stocks" : "main"); } },
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
    if (!root.OmegaExplain) { const x = document.createElement("script"); x.src = `${BASE}explain.js`; document.head.appendChild(x); }
    if (!store.get("omega.onb.tour", false)) setTimeout(() => tour(false), 700);
    else checklist();
    root.addEventListener("storage", checklist);
    setInterval(checklist, 4000);           // ticks items off as the visitor uses the page (trades, alerts)
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
  root.OmegaShell = { tour, page: HERE, pages: PAGES };
})(window);
