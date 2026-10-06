/* Alerts for every page: pop-ups with sounds for trades worth entering, trades to exit, profits to take and
   warnings. Clicking one opens the exact page, switches to the right market / list / speed and highlights the
   opportunity.

   * One open tab of the site is enough: it watches the published snapshots of US stocks, Forex, ICT and the
     Strategy lab every 3 minutes (only one tab does it, the others stay quiet).
   * Exchange coins, Small coins and DEX tokens work their ideas out inside the page, so their entry alerts come
     while that page is open; their trades are checked by the page as usual.
   * "My trades" on every page report take-profit, safety-exit, time's-up and warning advice here.
   * Nothing is sent anywhere: settings and "already told you" memory stay in this browser. Browsers only show
     pop-ups while a tab of the site is open (a closed browser can't be woken without a paid push server).
   * Never alerts on old data: a snapshot older than 45 minutes is ignored. */
(function (root) {
  "use strict";
  const SCRIPT = document.currentScript;
  const BASE = new URL(".", SCRIPT ? SCRIPT.src : location.href).href;            // the site's root folder
  const DATA = root.OMEGA_ALERT_DATA || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/data/";
  const PAGES = { main: "coins/", small: "small/", dex: "dex/", stocks: "stocks/", fx: "fx/", ict: "ict/", lab: "lab/", listings: "listings/", sniper: "sniper/", whales: "whales/", predict: "predict/" };
  const PAGE_NAME = { main: "Exchange coins", small: "Small coins", dex: "DEX tokens", stocks: "US stocks", fx: "Forex & gold", ict: "ICT setups", lab: "Strategy lab", listings: "New listings", sniper: "Sniper lab", whales: "Whale tracker", predict: "Prediction markets" };
  const KINDS = { enter: ["Trade to enter", "▲"], exit: ["Exit the trade", "■"], profit: ["Take profit", "★"], warning: ["Warning", "!"], news: ["New listing", "●"], whale: ["Whale move", "◆"] };
  const PANEL_TEXT = { enter: "New trades worth entering", exit: "Time to exit (safety exit, time's up, exit signals)", profit: "Take profit reached", warning: "Warnings on my trades", whale: "Whale moves: any change by a followed whale (information only, not proven)", news: "A coin starts trading on Binance, OKX or Gate.io" };
  const POLL_MS = 3 * 60 * 1000, LEAD_MS = 4 * 60 * 1000, STALE_MIN = 45, REPEAT_H = { enter: 12, exit: 6, profit: 6, warning: 6, news: 48, whale: 24 };
  const HIGH = { Strong: 2, Moderate: 1 };
  const WHALE_MIN = [10e3, 50e3, 100e3, 250e3, 500e3, 1e6, 2.5e6, 5e6, 10e6, 25e6];      // the size range a visitor can pick for whale moves
  const WHALE_MAX = [0, 100e3, 250e3, 500e3, 1e6, 2.5e6, 5e6, 10e6, 25e6, 50e6, 100e6];
  const MAX_TOASTS = 4, ONE_BY_ONE = 2;     // more new ideas than this at once on a page: one summary alert instead
  const TAB_ID = Math.random().toString(36).slice(2);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode: alerts still work, memory doesn't */ } },
  };
  const DEFAULTS = { on: false, sound: true, kinds: { enter: true, exit: true, profit: true, warning: true, news: true, whale: false }, minGrade: "Strong", whaleMin: 1e6, whaleMax: 0,
    pages: { main: true, small: true, dex: true, stocks: true, fx: true, ict: true, lab: true, listings: true, sniper: true, whales: true, predict: true } };
  let cfg = { ...DEFAULTS, ...store.get("omega.alerts.cfg", {}) };
  cfg.kinds = { ...DEFAULTS.kinds, ...(cfg.kinds || {}) }; cfg.pages = { ...DEFAULTS.pages, ...(cfg.pages || {}) };
  const saveCfg = () => { store.set("omega.alerts.cfg", cfg); paintBell(); };

  // ------------------------------------------------------------------ which page is this
  function currentPage() {
    if (root.OMEGA_MODE === "small") return "small";
    if (root.OMEGA_MODE === "home") return "home";
    const rel = location.href.replace(/[?#].*$/, "").replace(BASE, "");
    const first = rel.split("/")[0];
    return PAGES[first] !== undefined && first ? first : "main";       // site/coins/ is Exchange coins ("main")
  }
  const HERE = currentPage();
  function urlFor(page, focus, pick, tab) {
    const q = new URLSearchParams();
    if (focus) q.set("focus", focus);
    if (pick && pick.length) q.set("pick", JSON.stringify(pick));
    return `${BASE}${PAGES[page]}${q.toString() ? `?${q}` : ""}#/${tab || "ideas"}`;
  }

  // ------------------------------------------------------------------ sound
  let ctx = null;
  function unlockAudio() {
    try { ctx = ctx || new (root.AudioContext || root.webkitAudioContext)(); if (ctx.state === "suspended") ctx.resume(); } catch { ctx = null; }
  }
  const TONES = { news: [[523, 0], [659, 0.1], [784, 0.2]], enter: [[660, 0], [880, 0.14]], exit: [[740, 0], [494, 0.16]], profit: [[784, 0], [988, 0.12], [1319, 0.24]], warning: [[440, 0], [440, 0.22]], whale: [[392, 0], [523, 0.15], [392, 0.3]] };
  function beep(kind) {
    if (!cfg.sound || !ctx) return;
    try {
      const t0 = ctx.currentTime;
      for (const [f, dt] of TONES[kind] || TONES.enter) {
        const o = ctx.createOscillator(), g = ctx.createGain();
        o.type = kind === "warning" ? "square" : "sine"; o.frequency.value = f;
        g.gain.setValueAtTime(0.0001, t0 + dt); g.gain.exponentialRampToValueAtTime(kind === "warning" ? 0.12 : 0.2, t0 + dt + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + dt + 0.18);
        o.connect(g).connect(ctx.destination); o.start(t0 + dt); o.stop(t0 + dt + 0.2);
      }
    } catch { /* no sound available */ }
  }

  // ------------------------------------------------------------------ remembering what was already said
  function seen(key, kind) {
    const m = store.get("omega.alerts.seen", {}); const t = m[key];
    return t && Date.now() - t < (REPEAT_H[kind] || 6) * 3600e3;
  }
  function markSeen(key) {
    const m = store.get("omega.alerts.seen", {}); const now = Date.now();
    for (const k of Object.keys(m)) if (now - m[k] > 3 * 86400e3) delete m[k];
    m[key] = now; store.set("omega.alerts.seen", m);
  }

  // ------------------------------------------------------------------ showing an alert
  function css() {
    if (document.getElementById("omega-alerts-css")) return;
    const s = document.createElement("style"); s.id = "omega-alerts-css";
    s.textContent = `.oa-bell{display:inline-flex;align-items:center;gap:6px;cursor:pointer;border:1px solid var(--line);background:var(--surface);color:inherit;border-radius:999px;padding:6px 12px;font:inherit;font-size:.85rem}
.oa-bell[data-on="1"]{border-color:var(--good,#1a7f37);font-weight:600}
.oa-panel{position:fixed;z-index:1001;top:64px;right:16px;max-width:min(360px,calc(100vw - 32px));background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px;box-shadow:0 10px 30px rgba(0,0,0,.18);display:grid;gap:10px;font-size:.9rem}
.oa-panel label{display:flex;gap:8px;align-items:center}.oa-range{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:-4px 0 0 24px}.oa-range[hidden]{display:none}.oa-panel fieldset{border:1px solid var(--line);border-radius:8px;padding:8px 10px;display:grid;gap:4px}
.oa-toasts{position:fixed;z-index:1000;right:16px;bottom:16px;display:grid;gap:8px;max-width:min(380px,calc(100vw - 32px))}
.oa-toast{background:var(--surface);border:1px solid var(--line);border-left:6px solid var(--muted);border-radius:10px;padding:10px 12px;box-shadow:0 8px 24px rgba(0,0,0,.18);cursor:pointer;font-size:.9rem}
.oa-toast b{display:block}.oa-toast.enter{border-left-color:var(--good,#1a7f37)}.oa-toast.profit{border-left-color:#b7791f}.oa-toast.exit{border-left-color:var(--bad,#c62828)}.oa-toast.warning{border-left-color:#d97706}.oa-toast.news{border-left-color:#2563eb}.oa-toast.whale{border-left-color:#7c3aed}
.oa-toast .oa-x{float:right;border:0;background:none;color:inherit;cursor:pointer;font-size:1rem}
.alert-focus{outline:3px solid var(--good,#1a7f37);outline-offset:4px;animation:oa-pulse 1.2s ease-in-out 3}
@keyframes oa-pulse{50%{outline-color:transparent}}`;
    document.head.appendChild(s);
  }
  function toastBox() {
    let b = document.querySelector(".oa-toasts");
    if (!b) { b = document.createElement("div"); b.className = "oa-toasts"; b.setAttribute("aria-live", "polite"); document.body.appendChild(b); }
    return b;
  }
  function toast(a) {
    css();
    const el = document.createElement("div"); el.className = `oa-toast ${a.kind}`; el.setAttribute("role", "status");
    el.innerHTML = `<button class="oa-x" type="button" aria-label="Dismiss">×</button><b>${esc(KINDS[a.kind][1])} ${esc(KINDS[a.kind][0])}: ${esc(a.title)}</b><span>${esc(a.body)}</span><div class="small muted">${esc(PAGE_NAME[a.page] || "")} · click to open</div>`;
    el.addEventListener("click", (e) => { if (e.target.closest(".oa-x")) { el.remove(); return; } el.remove(); open(a); });
    const box = toastBox(); box.appendChild(el);
    while (box.children.length > MAX_TOASTS) box.firstChild.remove();
    setTimeout(() => el.remove(), 30000);
  }
  function open(a) {
    try { root.focus(); } catch { /* ignore */ }
    if (a.page === HERE) {
      applyFocus(a.focus, a.pick, a.tab);
    } else {
      location.href = a.url;
    }
  }
  function fire(a) {
    if (!cfg.on || !cfg.kinds[a.kind] || !cfg.pages[a.page]) return false;
    const key = `${a.page}|${a.key}`;
    if (seen(key, a.kind)) return false;
    markSeen(key);
    for (const k of a.also || []) markSeen(`${a.page}|${k}`);
    a.url = a.url || urlFor(a.page, a.focus, a.pick, a.tab);
    beep(a.kind);
    let shown = false;
    if (document.hidden && "Notification" in root && Notification.permission === "granted") {
      try {
        const n = new Notification(`${KINDS[a.kind][0]}: ${a.title}`, { body: `${a.body}\n${PAGE_NAME[a.page] || ""} · click to open`, tag: key, renotify: true, requireInteraction: a.kind !== "enter" });
        n.onclick = () => { n.close(); open(a); };
        shown = true;
      } catch { /* some phones only allow notifications from an installed app */ }
    }
    if (!shown || !document.hidden) toast(a);
    return true;
  }

  // ------------------------------------------------------------------ jumping to the opportunity
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function applyFocus(focus, pick, tab) {
    if (!focus && !(pick && pick.length)) return;
    await sleep(800);
    for (const sel of pick || []) {
      const b = document.querySelector(sel);
      if (b && b.getAttribute("aria-pressed") !== "true") { b.click(); await sleep(900); }
    }
    if (tab) { const t = document.querySelector(`nav.tabs button[data-tab="${tab}"]`); if (t && t.getAttribute("aria-selected") !== "true") t.click(); }
    if (!focus) return;
    const t0 = Date.now();
    while (Date.now() - t0 < 30000) {
      const el = [...document.querySelectorAll("[data-alert-key]")].find((x) => x.dataset.alertKey === focus);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        el.classList.remove("alert-focus"); void el.offsetWidth; el.classList.add("alert-focus");
        return;
      }
      await sleep(700);
    }
    toast({ kind: "warning", page: HERE, title: "That opportunity is no longer listed", body: "It expired or the price moved since the alert. The page shows what's current.", url: location.href });
  }
  function focusFromUrl() {
    const q = new URLSearchParams(location.search);
    const focus = q.get("focus"); let pick = [];
    try { pick = JSON.parse(q.get("pick") || "[]"); } catch { pick = []; }
    if (!focus && !pick.length) return;
    const tab = (location.hash.match(/^#\/([a-z]+)/) || [])[1];
    history.replaceState(null, "", location.pathname + location.hash);
    const go = () => applyFocus(focus, pick, tab);
    if (document.readyState === "complete") go(); else root.addEventListener("load", go);
  }

  // ------------------------------------------------------------------ what's worth an alert
  // the same scoring as site/engine.js and the pages
  const GRADES = [[6.5, "Strong"], [5.6, "Moderate"], [5.0, "Weak"], [-1, "Avoid - watch only"]];
  const gradeOf = (sc) => GRADES.find(([th]) => sc >= th)[1];
  const scoreFromR = (r) => 10 / (1 + Math.exp(-6 * r));
  const adjustR = (r, ru, adj) => (!adj || !adj.slope || !(ru > 0) ? r : r + adj.slope * (Math.log(ru) - adj.mean) / adj.sd);
  const gradeOk = (g) => (HIGH[g] || 0) >= (HIGH[cfg.minGrade] || 2);
  const unreliable = (q) => !!(q && q.check && q.check.reliable === false);
  const fresh = (s, maxMin) => s && s.generated_at && (Date.now() - Date.parse(s.generated_at)) / 60000 <= (maxMin || STALE_MIN);
  const pctTxt = (x) => (Number.isFinite(x) ? `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%` : "");

  const WATCH = {
    stocks: { file: "stocks/snapshot.json", ideas(s) {
      // scored exactly as the US stocks page does (site/stocks/app.js evaluate), with this visitor's currency fee,
      // and only the 5 ideas the page lists for each list and speed
      const fxFee = +store.get("omega.stk.fx", 0.01) || 0;
      const out = [];
      for (const [list, styles] of Object.entries(s.lists || {})) {
        const q = (s.quality || {})[list];
        if (unreliable(q)) continue;
        for (const [style, rows] of Object.entries(styles || {})) {
          const top = (rows || []).map((d) => {
            const c = d.spread + (s.sec_fee || 0.0000278) + 2 * fxFee;
            const sc = scoreFromR(adjustR(d.p * d.win_r - (1 - d.p) * d.loss_r - c / d.risk_unit, d.risk_unit, (q || {}).vol) - d.flag_penalty);
            return { d, sc };
          }).sort((a, b) => b.sc - a.sc).slice(0, 5);
          top.forEach(({ d, sc }) => { const g = gradeOf(sc); if (!gradeOk(g)) return;
            out.push({ score: sc, key: `idea-${style}-${d.symbol}`, short: `BUY ${d.symbol}`, title: `BUY ${d.symbol} (${g}, score ${sc.toFixed(1)})`,
              body: `${d.name || ""}: take profit ${pctTxt(d.take_profit_pct)}, safety exit ${pctTxt(-Math.abs(d.safety_exit_pct || 0))}.`,
              pick: [`#list button[data-list="${list}"]`, `#speed button[data-style="${style}"]`], tab: "ideas" }); });
        }
      }
      return out;
    } },
    fx: { file: "fx/snapshot.json", ideas(s) {
      // as the Forex page ranks them (site/fx/app.js): blocked ideas out, top 5 per list and speed
      if (unreliable(s.quality)) return [];
      const out = [];
      for (const [list, styles] of Object.entries(s.lists || {})) {
        for (const [style, rows] of Object.entries(styles || {})) {
          const top = (rows || []).filter((d) => !(d.hard || []).length).map((d) => ({ d, sc: scoreFromR(d.expected_r) })).sort((a, b) => b.sc - a.sc).slice(0, 5);
          top.forEach(({ d, sc }) => { const g = gradeOf(sc); if (!gradeOk(g)) return;
            out.push({ score: sc, key: `idea-${style}-${d.pair}-${d.side}`, short: `${String(d.side).toUpperCase()} ${d.label}`, title: `${String(d.side).toUpperCase()} ${d.label} (${g}, score ${sc.toFixed(1)})`,
              body: d.headline || `Take profit ${d.take_profit}, safety exit ${d.safety_exit}.`,
              pick: [`#list button[data-list="${list}"]`, `#speed button[data-style="${style}"]`], tab: "ideas" }); });
        }
      }
      return out;
    } },
    ict: { file: "ict/snapshot.json", ideas(s) {
      const out = [];
      for (const [k, c] of Object.entries(s.classes || {})) {
        if (unreliable((s.quality || {})[k])) continue;
        (c.setups || []).forEach((x) => { if (x.status !== "pending" || !gradeOk(x.grade) || (x.hard || []).length) return;
          out.push({ score: +x.score, key: `idea-${x.id}`, title: `${String(x.side).toUpperCase()} ${x.label} (${x.grade}, score ${(+x.score).toFixed(1)})`,
            body: `ICT setup: entry ${x.entry}, take profit ${x.target}, safety exit ${x.stop}. ${x.evidence || ""}.`,
            pick: [`#market button[data-market="${k}"]`], tab: "setups" }); });
      }
      return out;
    } },
    lab: { file: "lab/snapshot.json", ideas(s) {
      const out = [];
      const qOk = !unreliable(s.quality);
      (s.ideas || []).forEach((x) => { if (!qOk || x.level !== "good" || !gradeOk(x.grade)) return;
        out.push({ score: +x.score, key: `idea-${x.id}`, title: `${x.side > 0 ? "BUY" : "SELL"} ${x.label} (${x.grade}, score ${(+x.score).toFixed(1)})`,
          body: `${x.name}: chance it makes money ${Math.round(x.prob * 100)}%. ${x.exit_rule}`, tab: "" }); });
      (s.forming || []).forEach((f) => { if (!f.signal) return;
        out.push({ key: `forming-${f.sym}-${new Date(f.asof).toISOString().slice(0, 10)}`, title: `Likely BUY ${f.label} at today's close`,
          body: `Connors RSI(2) (held up in tests): 2-day RSI ${Math.round(f.rsi2)} if it closed now. The order goes in during the last minutes before 4 pm New York.`, tab: "" }); });
      (s.exits || []).forEach((e) => { const st = (s.strategies || {})[e.strategy]; if (!st || !st.stats || st.stats.level !== "good") return;
        out.push({ kind: "exit", key: `exit-${e.strategy}-${e.sym}-${e.t_in}`, title: `${e.name}: sell ${e.label}`,
          body: `The strategy exited (${e.reason}), ${pctTxt(e.net)} after costs. If you followed it, this is where it got out.`, tab: "" }); });
      return out;
    } },
  };
  // new entry ideas for one page: up to ONE_BY_ONE alerts, or one summary that opens the best of them
  function fireEntries(page, items) {
    if (!cfg.on || !cfg.pages[page]) return;
    const fresh = items.filter((it) => !seen(`${page}|${it.key}`, it.kind || "enter"));
    const entries = fresh.filter((it) => !it.kind || it.kind === "enter").sort((a, b) => (b.score || 0) - (a.score || 0));
    const moves = fresh.filter((x) => x.kind === "whale").sort((a, b) => (b.score || 0) - (a.score || 0));
    for (const it of fresh.filter((x) => x.kind && x.kind !== "enter" && x.kind !== "whale")) fire({ page, focus: it.key, ...it });
    if (moves.length > ONE_BY_ONE + 1 && cfg.kinds.whale) {
      const top = moves[0];
      fire({ kind: "whale", page, key: `batch-${top.key}-${moves.length}`, focus: top.focus, tab: top.tab, also: moves.map((x) => x.key),
        title: `${moves.length} whale moves in your size range`, body: `Biggest: ${top.title}. Also: ${moves.slice(1, 6).map((x) => x.short).join(", ")}${moves.length > 6 ? "…" : ""}.` });
    } else for (const it of moves) fire({ page, ...it });
    if (!cfg.kinds.enter) return;
    if (entries.length <= ONE_BY_ONE) { for (const it of entries) fire({ kind: "enter", page, focus: it.key, ...it }); return; }
    const top = entries[0];
    fire({ kind: "enter", page, key: `batch-${top.key}-${entries.length}`, focus: top.key, pick: top.pick, tab: top.tab, also: entries.map((x) => x.key),
      title: `${entries.length} new ${cfg.minGrade === "Strong" ? "Strong" : "Strong/Moderate"} ideas on ${PAGE_NAME[page]}`,
      body: `Best first: ${top.title}. Also: ${entries.slice(1, 6).map((x) => x.short || x.title.split(" (")[0]).join(", ")}${entries.length > 6 ? "…" : ""}.` });
  }
  const EXN = { binance: "Binance", okx: "OKX", gate: "Gate.io" };
  WATCH.listings = { file: "listings/snapshot.json", staleMin: 75, ideas(s) {
    const out = [];
    const qOk = !unreliable(s.quality);
    (s.ideas || []).forEach((x) => { if (!qOk || x.level !== "good" || !gradeOk(x.grade)) return;
      out.push({ score: +x.score, key: `idea-${x.id}`, short: `${x.side > 0 ? "BUY" : "SELL"} ${x.base}`, title: `${x.side > 0 ? "BUY" : "SELL"} ${x.base} on ${EXN[x.exchange]} (${x.grade}, score ${(+x.score).toFixed(1)})`,
        body: `${x.name}: worked ${Math.round(x.prob * 100)}% of the time in the test. ${x.side < 0 ? "If you hold it, selling is the low-risk version." : ""}`, tab: "" }); });
    (s.listings || []).forEach((r) => { if (!(r.age_d < 3 / 24)) return;
      out.push({ kind: "news", key: `listing-${r.exchange}-${r.symbol}`, title: `${r.base} just started trading on ${EXN[r.exchange]}`,
        body: "In the test, buying in the first hour lost money on average; selling after the first hour held up best. Open to see the numbers.", tab: "" }); });
    (s.upcoming || []).forEach((u) => { const mins = (u.list_ms - Date.now()) / 60000; if (!(mins > 0 && mins <= 60)) return;
      out.push({ kind: "news", key: `upcoming-${u.exchange}-${u.symbol}`, title: `${u.base} opens on ${EXN[u.exchange]} in ${Math.round(mins)} min`,
        body: "The first hours after a listing are the wildest. Open to see what usually happens next.", tab: "" }); });
    return out;
  } };
  // paper snipes: only once a plan has held up and the grade is good (until then everything there is watch-only)
  WATCH.sniper = { file: "sniper/snapshot.json", ideas(s) {
    if (unreliable(s.quality)) return [];
    return (s.ideas || []).filter((x) => x.evidence === "Held up in paper tests" && gradeOk(x.grade)).map((x) => ({ score: +x.score, key: `idea-${x.id}`,
      short: x.symbol, title: `${x.symbol} on ${x.chain} passed every scam check (${x.grade}, score ${(+x.score).toFixed(1)})`,
      body: `Paper snipe: opened ${Math.round(x.delay_min)} min ago, ${Math.round(x.prob * 100)}% of similar paper snipes made money. Check the address on DexScreener.`, tab: "" }));
  } };
  // whales: big new positions of the followed whales, only once the paper mirror has held up
  WATCH.whales = { file: "whales/snapshot.json", ideas(s) {
    if (!s.verdict || s.verdict.level !== "good") return [];
    return (s.big_changes || []).map((x) => ({ score: x.notional / 1e6, key: `chg-${x.addr}-${x.coin}-${x.kind}`, short: `${x.coin} ${x.side}`,
      title: `A followed whale ${x.kind} ${x.side} ${x.coin} ($${(x.notional / 1e6).toFixed(1)}M)`,
      body: `The paper mirror of last month's best whales has held up. Price ${x.price}; their entry ${x.entry}.`, tab: "" }));
  }, moves(s) {
    // any change by a followed whale whose money moved is inside the visitor's range: information, not a proven signal
    if (!cfg.kinds.whale) return [];
    const lo = +cfg.whaleMin || 0, hi = +cfg.whaleMax || Infinity;
    const W = { opened: "opened", closed: "closed", flipped: "flipped to", added: "added to", cut: "cut" };
    return (s.changes || []).filter((x) => { const m = x.moved ?? x.notional; return m >= lo && m <= hi; }).map((x) => {
      const m = x.moved ?? x.notional; const who = x.name || `${String(x.addr).slice(0, 6)}…`;
      const what = x.kind === "closed" ? `closed ${x.coin}` : `${W[x.kind] || x.kind} ${x.side} ${x.coin}`;
      return { kind: "whale", score: m, key: `wm-${s.generated_at}-${x.addr}-${x.coin}-${x.kind}`, focus: `chg-${x.addr}-${x.coin}-${x.kind}`,
        short: `${x.kind} ${x.coin} ${money(m)}`, title: `Whale ${who} ${what} (${money(m)})`,
        body: `${x.kind === "closed" ? "" : `Position now ${money(x.notional)}. `}Price ${x.price}. Information only: copying single whale moves didn't pay in our tests.`, tab: "" };
    });
  } };
  const money = (v) => (v >= 1e6 ? `$${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)}M` : `$${Math.round(v / 1e3)}k`);
  async function poll() {
    if (!cfg.on) return;
    const lead = store.get("omega.alerts.lead", null);
    if (lead && lead.id !== TAB_ID && Date.now() - lead.at < LEAD_MS) return;            // another tab is watching
    store.set("omega.alerts.lead", { id: TAB_ID, at: Date.now() });
    for (const [page, w] of Object.entries(WATCH)) {
      if (!cfg.pages[page]) continue;
      try {
        const r = await fetch(`${DATA}${w.file}?t=${Date.now()}`, { cache: "no-store" });
        if (!r.ok) continue;
        const s = await r.json();
        if (!fresh(s, w.staleMin)) continue;                                                            // never alert on old data
        fireEntries(page, w.ideas(s).concat(w.moves ? w.moves(s) : []));
      } catch { /* try again next round */ }
    }
  }

  // ------------------------------------------------------------------ calls from the pages
  // ideas worked out inside the page (Exchange coins, Small coins, DEX): [{key, title, body, grade, pick, tab}]
  function ideas(page, list, quality) {
    if (unreliable(quality)) return;
    fireEntries(page, (list || []).filter((it) => gradeOk(it.grade)).map((it) => ({ tab: "ideas", ...it })));
  }
  // advice on one of "My trades" (site/engine.js advise / the pages' own advice)
  function classify(action, level) {
    const a = String(action || "").toLowerCase();
    if (a.includes("take profit") || a.includes("profit reached")) return "profit";
    if (a.includes("exit now") || a.includes("safety exit") || a.includes("sell now") || a.includes("time's up") || a.includes("sell when") || a.includes("close it")) return "exit";
    if (level === "bad" || level === "warn") return "warning";
    return null;
  }
  function trade(page, t, adv) {
    const kind = classify(adv && adv.action, adv && adv.level);
    if (!kind || !t) return;
    const id = t.id || `${t.symbol || t.pair || t.sym || t.token}`;
    fire({ kind, page, key: `trade-${id}-${adv.action}`, focus: `trade-${id}`, tab: "trades",
      title: `${adv.action}: ${t.label || t.symbol || t.pair || t.sym || "your trade"}`, body: adv.why || "" });
  }

  // ------------------------------------------------------------------ the bell and its settings
  function paintBell() {
    const b = document.querySelector(".oa-bell"); if (!b) return;
    b.dataset.on = cfg.on ? "1" : "0";
    b.innerHTML = `<span aria-hidden="true">${cfg.on ? "🔔" : "🔕"}</span><span class="oa-txt">Alerts ${cfg.on ? "on" : "off"}</span>`;
    b.setAttribute("aria-label", `Alerts ${cfg.on ? "on" : "off"}`);
  }
  function panel() {
    css();
    let p = document.querySelector(".oa-panel");
    if (p) { p.remove(); return; }
    p = document.createElement("div"); p.className = "oa-panel"; p.setAttribute("role", "dialog"); p.setAttribute("aria-label", "Alert settings");
    const perm = !("Notification" in root) ? "This browser can't show pop-up notifications; alerts appear inside the page instead."
      : Notification.permission === "denied" ? "Pop-ups are blocked for this site in your browser settings; alerts appear inside the page instead."
        : Notification.permission === "granted" ? "Pop-ups allowed." : "Turning alerts on will ask your browser for permission to show pop-ups.";
    p.innerHTML = `<b>Alerts</b>
      <label><input type="checkbox" data-k="on" ${cfg.on ? "checked" : ""}> Alerts on</label>
      <label><input type="checkbox" data-k="sound" ${cfg.sound ? "checked" : ""}> Play sounds</label>
      <fieldset><legend class="small">Tell me about</legend>${Object.keys(KINDS).map((k) => `<label><input type="checkbox" data-kind="${k}" ${cfg.kinds[k] ? "checked" : ""}> ${PANEL_TEXT[k]}</label>`).join("")}</fieldset>
      <div class="oa-range" ${cfg.kinds.whale ? "" : "hidden"}><span class="small">Whale moves from</span> <select data-k="whaleMin" aria-label="Smallest whale move">${WHALE_MIN.map((v) => `<option value="${v}" ${+cfg.whaleMin === v ? "selected" : ""}>${money(v)}</option>`).join("")}</select>
        <span class="small">up to</span> <select data-k="whaleMax" aria-label="Largest whale move">${WHALE_MAX.map((v) => `<option value="${v}" ${+cfg.whaleMax === v ? "selected" : ""}>${v ? money(v) : "no limit"}</option>`).join("")}</select></div>
      <label>New trades from <select data-k="minGrade"><option value="Strong" ${cfg.minGrade === "Strong" ? "selected" : ""}>Strong only</option><option value="Moderate" ${cfg.minGrade === "Moderate" ? "selected" : ""}>Strong and Moderate</option></select></label>
      <fieldset><legend class="small">Pages</legend>${Object.keys(PAGES).map((k) => `<label><input type="checkbox" data-page="${k}" ${cfg.pages[k] ? "checked" : ""}> ${PAGE_NAME[k]}</label>`).join("")}</fieldset>
      <button class="btn" type="button" data-test>Send a test alert</button>
      <p class="small muted">${perm} Alerts work while any page of this site is open in a tab (it can be in the background). US stocks, Forex, ICT and Strategy lab are watched from any page; Exchange coins, Small coins and DEX ideas while that page is open. Your trades: "My trades" on each page. Whale moves are information only: copying single moves didn't pay in our tests. Only fresh data, never old prices. Grades must be working (they switch off automatically when they're not), and alerts are not advice.</p>`;
    p.addEventListener("change", async (e) => {
      const x = e.target;
      if (x.dataset.k === "on") {
        cfg.on = x.checked;
        if (cfg.on) { unlockAudio(); if ("Notification" in root && Notification.permission === "default") { try { await Notification.requestPermission(); } catch { /* ignore */ } } poll(); }
      } else if (x.dataset.k === "sound") { cfg.sound = x.checked; if (x.checked) unlockAudio(); }
      else if (x.dataset.k === "minGrade") cfg.minGrade = x.value;
      else if (x.dataset.k === "whaleMin" || x.dataset.k === "whaleMax") {
        cfg[x.dataset.k] = +x.value;
        if (cfg.whaleMax && cfg.whaleMax < cfg.whaleMin) { cfg.whaleMax = 0; const mx = p.querySelector('[data-k="whaleMax"]'); if (mx) mx.value = "0"; }
      } else if (x.dataset.kind) { cfg.kinds[x.dataset.kind] = x.checked; if (x.dataset.kind === "whale") p.querySelector(".oa-range").hidden = !x.checked; }
      else if (x.dataset.page) cfg.pages[x.dataset.page] = x.checked;
      saveCfg();
    });
    p.querySelector("[data-test]").addEventListener("click", () => {
      unlockAudio();
      const was = cfg.on; cfg.on = true;
      fire({ kind: "enter", page: PAGES[HERE] !== undefined ? HERE : "main", key: `test-${Date.now()}`, title: "Test alert", body: "This is how a new trade alert looks and sounds. Clicking it opens the opportunity.", tab: "" });
      cfg.on = was;
    });
    document.body.appendChild(p);
  }
  function mountBell() {
    css();
    const host = document.querySelector("header.top .top-right") || document.querySelector("header.top");
    if (!host || host.querySelector(".oa-bell")) return;
    const b = document.createElement("button"); b.type = "button"; b.className = "oa-bell"; b.setAttribute("aria-haspopup", "dialog");
    b.addEventListener("click", () => { unlockAudio(); panel(); });
    host.insertBefore(b, host.firstChild);
    paintBell();
  }
  document.addEventListener("click", (e) => { const p = document.querySelector(".oa-panel"); if (p && !p.contains(e.target) && !e.target.closest(".oa-bell")) p.remove(); });
  document.addEventListener("pointerdown", () => { if (cfg.on) unlockAudio(); }, { once: true });
  root.addEventListener("storage", (e) => { if (e.key === "omega.alerts.cfg") { cfg = { ...DEFAULTS, ...store.get("omega.alerts.cfg", {}) }; paintBell(); } });

  function start() {
    mountBell(); focusFromUrl();
    setTimeout(poll, 5000);
    setInterval(poll, POLL_MS);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();

  root.OmegaAlerts = { ideas, trade, fire, classify, urlFor, page: HERE, on: () => !!cfg.on, _poll: poll, _cfg: () => cfg };
})(window);
