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
    main: { path: "coins/", name: "Exchange coins", group: "ideas", risk: "Medium risk", kind: "Live ideas", blurb: "The best-scoring coins on big exchanges like Binance, re-ranked every few minutes.", action: "Pick a speed, read the top idea, and only act on Strong or Moderate grades." },
    small: { path: "small/", name: "Small coins", group: "ideas", risk: "High risk", kind: "Live ideas", blurb: "Smaller, faster-moving coins: bigger swings both ways, scored the same honest way.", action: "Use smaller amounts than usual: these swing hard." },
    dex: { path: "dex/", name: "DEX tokens", group: "ideas", risk: "High risk", kind: "Live ideas", blurb: "Tokens on decentralised exchanges, scam-checked before they're ever suggested.", action: "Keep the safety level on Standard and copy the real token address from the card." },
    listings: { path: "listings/", name: "New listings", group: "lab", risk: "Very high risk", kind: "Tested rules", blurb: "Coins just listed on big exchanges, and what really happens after a listing.", action: "Only act on the tested ideas at the top; most new listings fall." },
    sniper: { path: "sniper/", name: "Sniper lab", group: "lab", risk: "Paper only", kind: "Research", blurb: "Brand-new DEX tokens, scam-checked and paper-sniped: does it pay after the bots?", action: "Watch only: no real-money sniping until the verdict says it holds up." },
    accum: { path: "accum/", name: "Accumulation lab", group: "lab", risk: "Paper only", kind: "Research", blurb: "Who is quietly buying before the price moves, and old coins that may recover: the evidence, checked and tested.", action: "Watch only: a watchlist entry is research, not a trade. Read the evidence and the warnings." },
    hayes: { path: "hayes/", name: "Recovery engine A", group: "lab", risk: "Paper only", kind: "Research", blurb: "Coins 75–90% down, bought only when dollar liquidity is rising: tested on every coin since 2019 first.", action: "Watch only: it trades nothing, not even on paper, unless it passes its history tests." },
    deep: { path: "deep/", name: "Deep distress engine B", group: "lab", risk: "Paper only", kind: "Research", blurb: "Coins 92–98% down with a measured catalyst: data coverage checked, then tested on history.", action: "Watch only: it trades nothing, not even on paper, unless it passes its history tests." },
    mev: { path: "mev/", name: "Arbitrage lab", group: "lab", risk: "Paper only", kind: "Research", blurb: "Solana arbitrage against real bots, fees and Jito tips: does an opportunity survive long enough to take?", action: "Watch only: shadow mode, nothing is ever signed or sent." },
    stocks: { path: "stocks/", name: "US stocks", group: "ideas", risk: "Medium risk", kind: "Live ideas", blurb: "The most-traded US stocks and a high-volatility list, during market hours.", action: "Start with Large stocks; ideas appear 9:30–16:00 New York time." },
    fx: { path: "fx/", name: "Forex & gold", group: "ideas", risk: "Medium risk", kind: "Live ideas", blurb: "Currency pairs, gold and silver, buy or sell, checked for manipulation.", action: "Start with the major pairs and avoid the news windows." },
    ict: { path: "ict/", name: "ICT setups", group: "lab", risk: "Medium risk", kind: "Live setups", blurb: "Inner Circle Trader setups coded exactly, with exact entries and how they really did.", action: "Place the limit order only when the grade and test evidence are good." },
    lab: { path: "lab/", name: "Strategy lab", group: "lab", risk: "Risk varies", kind: "Tested ideas", blurb: "Famous public strategies re-tested honestly, and which ones still hold up.", action: "Check the Scoreboard: only strategies marked Held up give ideas." },
    predict: { path: "predict/", name: "Prediction markets", group: "lab", risk: "Paper only", kind: "Research", blurb: "Kalshi and Polymarket: favourites that may be underpriced, tested on paper at real prices.", action: "Information only until 50 paper trades have settled." },
    results: { path: "results/", name: "Results", group: "proof", risk: "Paper only", kind: "Every record", blurb: "Every section's live paper record on one screen, after every cost, against random picks.", action: "See what is working, what isn't, and what's too early to tell." },
    report: { path: "report/", name: "Weekly report", group: "proof", risk: "Paper only", kind: "Week by week", blurb: "Each week's finished paper trades in every section, after every cost, against random picks.", action: "See how the last week went, losers included." },
    status: { path: "status/", name: "System status", group: "proof", risk: "Paper only", kind: "Watchdog", blurb: "Is every page up to date and every background job running? Checked every 15 minutes.", action: "Check here if a page looks out of date." },
    legal: { path: "legal/", name: "Legal", group: "proof", risk: "Draft", kind: "Terms, privacy, risk", blurb: "Terms of use, privacy and risk disclosure (drafts pending legal review).", action: "Read before relying on anything here." },
    challenge: { path: "challenge/", name: "$100 DEX challenge", group: "proof", risk: "Paper only", kind: "Paper account", blurb: "A $100 paper account trading the DEX list like a person without bots, paying every real cost.", action: "Watch how it does day by day: every step is written down." },
    "challenge-small": { path: "challenge-small/", name: "$100 Small-coin challenge", group: "proof", risk: "Paper only", kind: "Paper account", blurb: "A $100 paper account trading the Small coins Day ideas on Binance, with orders anyone can copy by hand.", action: "Copy the trades by hand if you like: every step says exactly what to do." },
    carry: { path: "carry/", name: "$100 funding account", group: "proof", risk: "Paper only", kind: "Paper account", blurb: "A $100 paper account that holds coins with an equal futures short and collects the funding payments.", action: "Watch the funding come in: price moves cancel out." },
    whales: { path: "whales/", name: "Whale tracker", group: "lab", risk: "Paper only", kind: "Research", blurb: "Last month's best big traders, what they hold now, and a paper copy of them.", action: "Information only until the 30-day verdict is in." },
  };
  // the menu: what the visitor wants to trade, then the strategies and research that cut across markets
  const GROUPS = [
    { id: "proof", name: "Proof", blurb: "Is it working? Every record, live" },
    { id: "ideas", name: "Trade ideas", blurb: "Live, scored ideas for crypto, stocks and forex" },
    { id: "lab", name: "Lab", blurb: "Research: strategies and experiments being tested" },
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
    if (["results", "report", "challenge", "challenge-small"].includes(HERE)) {        // the shareable records (D102)
      const sh = document.createElement("button"); sh.type = "button"; sh.className = "btn share"; sh.dataset.share = ""; sh.textContent = "Share";
      metaBox.appendChild(sh);
    }
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
    staleBanner();
    proofBar();
  }

  // one small icon per page (D113): what kind of page it is at a glance
  const ICON = {
    chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>', cal: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    pulse: '<path d="M2 12h4l3-8 4 16 3-8h6"/>', wallet: '<rect x="2" y="6" width="20" height="14" rx="3"/><path d="M16 13h2M2 10h20M6 6l9-3 2 3"/>',
    coin: '<circle cx="12" cy="12" r="9"/><path d="M9 9.5c0-1.4 1.3-2.5 3-2.5s3 1.1 3 2.5-1.3 2-3 2.5-3 1.1-3 2.5 1.3 2.5 3 2.5 3-1.1 3-2.5M12 5v2M12 17v2"/>',
    small: '<circle cx="8" cy="15" r="5"/><circle cx="17" cy="8" r="4"/>', dex: '<path d="M7 7h11l-3-3M17 17H6l3 3"/>',
    stocks: '<path d="M3 17l6-6 4 4 8-8M15 7h6v6"/>', fx: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/>',
    flask: '<path d="M9 3h6M10 3v6l-6 10a2 2 0 0 0 2 3h12a2 2 0 0 0 2-3l-6-10V3M7 15h10"/>', target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    spark: '<path d="M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z"/>', layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>', dice: '<rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="8.5" cy="8.5" r="1"/><circle cx="15.5" cy="15.5" r="1"/><circle cx="12" cy="12" r="1"/>',
    wave: '<path d="M2 15c3 0 3-4 6-4s3 4 6 4 3-4 6-4M2 20c3 0 3-4 6-4s3 4 6 4 3-4 6-4"/>', bolt: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
    doc: '<path d="M6 2h9l5 5v15H6z"/><path d="M14 2v6h6M9 13h8M9 17h8"/>', shield: '<path d="M12 2l8 3v6c0 5-3.5 9-8 11-4.5-2-8-6-8-11V5z"/><path d="M9 12l2 2 4-4"/>',
  };
  const PAGE_ICON = { results: "chart", report: "cal", status: "pulse", challenge: "wallet", "challenge-small": "wallet", carry: "wallet", main: "coin",
    small: "small", dex: "dex", stocks: "stocks", fx: "fx", ict: "target", lab: "flask", listings: "spark", sniper: "target", predict: "dice",
    whales: "wave", mev: "bolt", accum: "layers", hayes: "chart", deep: "chart", legal: "doc" };
  const icon = (k, g) => `<span class="ni-ico" data-g="${g}" aria-hidden="true"><svg viewBox="0 0 24 24">${ICON[PAGE_ICON[k]] || ICON.shield}</svg></span>`;

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
        a.innerHTML = `${icon(k, p.group)}<span class="ni-name">${esc(p.name)}${k === HERE ? '<span class="ni-here">You are here</span>' : ""}</span><span class="ni-blurb">${esc(p.blurb)}</span><span class="ni-tags"><span>${esc(p.kind)}</span><span class="${/Very high|High/.test(p.risk) ? "hot" : /Paper/.test(p.risk) ? "paper" : ""}">${esc(p.risk)}</span></span>`;
        menu.appendChild(a);
      }
      const btn = dd.querySelector(".nav-trigger");
      btn.addEventListener("click", (e) => {
        e.stopPropagation(); const open = !dd.classList.contains("open"); closeMenus(); dd.classList.toggle("open", open); btn.setAttribute("aria-expanded", String(open));
        if (open) {                                     // keep the whole panel on screen (D113)
          menu.style.left = ""; const r = menu.getBoundingClientRect();
          if (r.right > innerWidth - 12) menu.style.left = `${-8 - (r.right - innerWidth + 12)}px`;
        }
      });
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
    // phones: one sticky "On this page" button that opens the full list (D113), instead of a row that hides most sections
    const jump = document.createElement("div"); jump.className = "jump";
    jump.innerHTML = `<button type="button" class="jump-btn" aria-expanded="false"><span>On this page</span><b></b><span aria-hidden="true">▾</span></button><nav class="jump-list" aria-label="On this page"></nav>`;
    const list = jump.querySelector(".jump-list"), jb = jump.querySelector(".jump-btn"), cur = jb.querySelector("b");
    [...bar.querySelectorAll("a")].forEach((a, i) => { const c = a.cloneNode(true); c.innerHTML = `<span class="n">${i + 1}</span>${esc(a.textContent)}`; list.appendChild(c); });
    cur.textContent = bar.querySelector("a").textContent;
    const setOpen = (o) => { jump.classList.toggle("open", o); jb.setAttribute("aria-expanded", String(o)); };
    jb.addEventListener("click", (e) => { e.stopPropagation(); setOpen(!jump.classList.contains("open")); });
    list.addEventListener("click", () => setOpen(false));
    document.addEventListener("click", (e) => { if (!e.target.closest(".jump")) setOpen(false); });
    bar.before(jump);
    if (cards.length > 6) { bar.classList.add("many"); jump.classList.add("many"); }   // too many for one row of chips
    const links = [...bar.querySelectorAll("a"), ...list.querySelectorAll("a")];
    if ("IntersectionObserver" in root) {
      const io = new IntersectionObserver((es) => {
        for (const e of es) if (e.isIntersecting) {
          for (const a of links) a.classList.toggle("on", a.getAttribute("href") === `#${e.target.id}`);
          const on = bar.querySelector("a.on"); if (on) cur.textContent = on.textContent;
        }
      }, { rootMargin: "-30% 0px -60% 0px" });
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
    const l = document.createElement("div"); l.className = "foot-links small";
    l.innerHTML = `<a href="${BASE}results/">Results</a> · <a href="${BASE}report/">Weekly report</a> · <a href="${BASE}status/">System status</a> · <a href="${BASE}legal/terms.html">Terms</a> · <a href="${BASE}legal/privacy.html">Privacy</a> · <a href="${BASE}legal/risk.html">Risk disclosure</a>`;
    b.after(l);
  }

  // ------------------------------------------------------------------ watchdog: say so when this page's data is stuck (D98)
  const SUMMARY = (root.OMEGA_REPO || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/main/state/") + "summary.json";
  async function staleBanner() {
    if (!PAGES[HERE] || HERE === "status" || HERE === "results") return;
    try {
      const r = await fetch(`${SUMMARY}?t=${Math.floor(Date.now() / 300000)}`);
      if (!r.ok) return;
      const S = await r.json();
      const s = (S.sections || []).find((x) => x.key === HERE);
      if (!s || !s.stale) return;
      const h = s.age_min == null ? "a long time" : s.age_min < 120 ? `${s.age_min} minutes` : `${Math.round(s.age_min / 60)} hours`;
      const b = document.createElement("div"); b.className = "banner warn"; b.setAttribute("role", "status");
      b.innerHTML = `<b>This page's data hasn't updated for ${esc(h)}.</b> The watchdog has flagged it and the owner has been notified; what you see may be out of date. <a href="${BASE}status/">System status</a>`;
      const hero = document.querySelector(".page-hero");
      (hero || document.querySelector(".wrap") || document.body).insertAdjacentElement(hero ? "afterend" : "afterbegin", b);
    } catch { /* the banner is a courtesy: never break the page */ }
  }

  // ------------------------------------------------------------------ the answer first (D115-D118): proven or testing, the market, the best idea
  const PROOF_PAGES = new Set(["main", "small", "dex", "stocks", "fx", "ict", "lab", "listings", "sniper", "predict", "whales", "accum", "hayes", "deep"]);
  const MARKET_PAGES = new Set(["main", "small", "dex", "listings", "sniper", "whales", "lab", "ict", "accum"]);
  const pctS = (x) => (Number.isFinite(x) ? `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(2)}%` : "–");
  function marketText(m) {
    if (!m || !m.now) return null;
    const fall = m.now.falling;
    const since = m.now.since ? new Date(m.now.since) : null;
    const when = since ? `since ${since.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })}` : "";
    const filt = m.active ? "crypto buy ideas are paused" : (m.test && m.test.enabled) ? "the filter is on" : "the filter was tested and is off";
    return { cls: fall ? "warn" : "good", head: `Market ${fall ? "falling" : "rising"}`, text: `Bitcoin is ${fall ? "below" : "above"} its 7-day average ${when}; ${filt}.`,
      title: (m.test && m.test.verdict) || "" };
  }
  async function proofBar() {
    const G = root.OMEGA_GATE;
    if (!G || !PROOF_PAGES.has(HERE)) return;
    await G.ready;
    const s = G.section(HERE);
    if (!s) return;
    const units = ((G.proof && G.proof.units) || []).filter((u) => u.section === HERE);
    const why = units.length === 1 ? units[0].reason : "";
    const head = { proven: "Proven", partly: "Partly proven", testing: "Testing only" }[s.status];
    const text = s.status === "proven" ? "Passed our honest tests: made money after costs, beat random picks by more than luck, and still works recently."
      : s.status === "partly" ? `Only <b>${esc(s.proven_units.join(", "))}</b> passed our honest tests. The other ideas here are greyed: testing only, not to act on.`
      : `${why ? esc(why) + " " : ""}Its ideas are shown greyed, to learn from: not to act on.`;
    const bar = document.createElement("section"); bar.className = "answer-bar"; bar.setAttribute("aria-label", "The answer first");
    const mk = MARKET_PAGES.has(HERE) ? marketText(G.market) : null;
    const b = G.best || {};
    let rec = null;
    try { const r = await fetch(`${SUMMARY}?t=${Math.floor(Date.now() / 300000)}`); if (r.ok) rec = ((await r.json()).sections || []).find((x) => x.key === HERE); } catch { rec = null; }
    const recVal = (x) => (!Number.isFinite(x) ? "–" : rec.unit === "R" ? `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(2)} R` : pctS(x));
    const recItem = rec ? `<a class="ab-item ${rec.status === "ahead" ? "good" : rec.status === "behind" ? "warn" : ""}" href="${BASE}results/"><span class="ab-k">Record so far</span>
      <span class="ab-v">${rec.n ? `${recVal(rec.avg)} a trade` : "Too early"}</span><span class="ab-t">${rec.n ? `${rec.n.toLocaleString()} finished, after every cost · random picks ${recVal(rec.random)}` : "No finished trades yet."}</span></a>` : "";
    const bestTxt = b.idea ? `<b>${esc(b.idea.title)}</b> · ${esc((b.idea.record || {}).name || "")}`
      : (b.waiting || []).length ? "A proven idea is waiting for the market to stop falling."
      : `${b.considered != null ? `${b.considered} live ideas checked; ` : ""}none comes from a part that passed our tests.`;
    bar.innerHTML = `<div class="ab-item ab-proof ${s.status}"><span class="ab-k">This section</span><span class="ab-v">${esc(head)}</span><span class="ab-t">${text} <a href="${BASE}results/#proof">How we test →</a></span></div>
      ${mk ? `<div class="ab-item ${mk.cls}" title="${esc(mk.title)}"><span class="ab-k">Market</span><span class="ab-v">${esc(mk.head)}</span><span class="ab-t">${esc(mk.text)}</span></div>` : ""}
      ${recItem}
      <a class="ab-item ab-best ${b.idea ? "good" : ""}" href="${BASE}#best"><span class="ab-k">Best idea right now</span><span class="ab-v">${b.idea ? "One idea" : "Sitting in cash"}</span><span class="ab-t">${bestTxt} →</span></a>`;
    const after = document.querySelector(".page-hero + .banner.warn") || document.querySelector(".page-hero");
    if (!after) return;
    after.after(bar);
    document.body.classList.add(`proof-${s.status}`);
    markIdeas(G, s);
    let queued = false;
    new MutationObserver(() => { if (!queued) { queued = true; requestAnimationFrame(() => { queued = false; markIdeas(G, s); }); } })
      .observe(document.body, { childList: true, subtree: true });
  }
  // phones (D118): every data table becomes a stack of labelled cards instead of scrolling sideways
  function stackTables() {
    for (const t of document.querySelectorAll("table:not(.stack):not(.no-stack)")) {
      const head = t.tHead && t.tHead.rows[t.tHead.rows.length - 1];
      if (!head) { if (!t.classList.contains("rows-wrap") && [...t.rows].some((r) => r.cells.length >= 3)) t.classList.add("rows-wrap"); continue; }
      if (head.cells.length < 3) continue;
      const labels = [];
      for (const c of head.cells) for (let i = 0; i < (c.colSpan || 1); i++) labels.push(c.textContent.trim());
      for (const body of t.tBodies) for (const row of body.rows) {
        let i = 0;
        for (const c of row.cells) { if (!c.hasAttribute("data-label")) c.setAttribute("data-label", labels[i] || ""); i += c.colSpan || 1; }
      }
      t.classList.add("stack");
    }
  }
  let stackQueued = false;
  function watchTables() {
    stackTables();
    new MutationObserver(() => { if (!stackQueued) { stackQueued = true; requestAnimationFrame(() => { stackQueued = false; stackTables(); }); } })
      .observe(document.body, { childList: true, subtree: true });
  }

  // grey every idea card that isn't from a proven unit (the visitor's own trades are never greyed)
  function markIdeas(G, s) {
    for (const c of document.querySelectorAll(".card:not(.trade)")) {
      const h = c.querySelector(".idea-head");
      if (!h || h.closest(".card") !== c || c.dataset.proofDone) continue;
      c.dataset.proofDone = "1";
      const ok = c.dataset.unit ? G.proven(HERE, c.dataset.unit) : s.status === "proven";
      if (ok) continue;
      c.classList.add("testing-idea");
      const r = document.createElement("div"); r.className = "testing-ribbon";
      r.innerHTML = "<b>Testing only</b> · this hasn't passed our honest tests: not a recommendation";
      c.prepend(r);
    }
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

  // ------------------------------------------------------------------ Share buttons (D102): the phone's share sheet, or copy the link
  async function share(btn) {
    const url = location.href.replace(/[?#].*$/, "");
    const desc = document.querySelector('meta[name="description"]')?.content || "";
    try {
      if (navigator.share) { await navigator.share({ title: document.title, text: desc, url }); return; }
      await navigator.clipboard.writeText(url);
      btn.textContent = "Link copied";
    } catch (e) {
      if (e && e.name === "AbortError") return;             // the visitor closed the share sheet
      btn.textContent = "Copy this page's address to share it";
    }
    setTimeout(() => { btn.textContent = "Share"; }, 2500);
  }

  function start() {
    buildShell();
    watchTables();
    document.addEventListener("click", (e) => { const b = e.target.closest("[data-share]"); if (b) share(b); });
    if (!root.OmegaExplain) { const x = document.createElement("script"); x.src = `${BASE}explain.js`; document.head.appendChild(x); }
    if (!store.get("omega.onb.tour", false)) setTimeout(() => tour(false), 700);
    else checklist();
    root.addEventListener("storage", checklist);
    if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost")) navigator.serviceWorker.register(`${BASE}sw.js`, { scope: BASE }).catch(() => {});   // installable app (D101)
    setInterval(checklist, 4000);           // ticks items off as the visitor uses the page (trades, alerts)
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
  root.OmegaShell = { tour, page: HERE, pages: PAGES };
})(window);
