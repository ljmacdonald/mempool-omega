/* Ask Omega: the front page. A chat box that answers in plain English from the site's own published data (the same
   files every page reads): today's ideas per market, any coin / stock / currency pair by name, what the whales hold,
   the market mood, how the ideas really did, and what any term means (the glossary in explain.js).
   No AI service and no keys: answers are built from the data, so they never invent a number. Each trade answer
   carries its grade, its exits and a link to the exact page; grades that are switched off are said to be. */
(function (root) {
  "use strict";
  const REPO = root.OMEGA_REPO || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/main/state/";
  const DATA = root.OMEGA_DATA || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/data/";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fin = (x) => typeof x === "number" && Number.isFinite(x);
  const pct = (x, d = 1) => (fin(x) ? `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(d)}%` : "–");
  const px = (x) => (!fin(x) ? "–" : x >= 1000 ? x.toLocaleString("en-US", { maximumFractionDigits: 2 }) : x >= 1 ? x.toPrecision(5).replace(/\.?0+$/, "") : x.toPrecision(4));
  const usdM = (x) => (!fin(x) ? "–" : x >= 1e6 ? `$${(x / 1e6).toFixed(1)}M` : `$${Math.round(x / 1e3)}k`);
  const when = (t) => { const d = new Date(t); return Number.isFinite(d.getTime()) ? d.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "–"; };
  const ago = (t) => { const m = Math.round((Date.now() - Date.parse(t)) / 60000); return !Number.isFinite(m) ? "" : m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`; };
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode: no history */ } },
  };

  // ------------------------------------------------------------------ data (cached for 3 minutes)
  const SRC = {
    main: REPO + "suggestions/latest_short.json", small: REPO + "suggestions/small_latest_short.json",
    mainBoard: REPO + "suggestions/scoreboard.json", smallBoard: REPO + "suggestions/small_scoreboard.json",
    stocks: DATA + "stocks/snapshot.json", fx: DATA + "fx/snapshot.json", lab: DATA + "lab/snapshot.json",
    listings: DATA + "listings/snapshot.json", sniper: DATA + "sniper/snapshot.json", whales: DATA + "whales/snapshot.json", predict: DATA + "predict/snapshot.json",
  };
  const cache = {};
  async function get(k) {
    const c = cache[k]; if (c && Date.now() - c.at < 180e3) return c.v;
    try { const r = await fetch(`${SRC[k]}?t=${Math.floor(Date.now() / 60e3)}`, { cache: "no-store" }); if (!r.ok) throw new Error(r.status); const v = await r.json(); cache[k] = { at: Date.now(), v }; return v; } catch { return null; }
  }
  const unreliable = (q) => !!(q && q.check && q.check.reliable === false);
  const PAGE = { main: ["coins/", "Exchange coins"], small: ["small/", "Small coins"], stocks: ["stocks/", "US stocks"], fx: ["fx/", "Forex & gold"], lab: ["lab/", "Strategy lab"],
    listings: ["listings/", "New listings"], sniper: ["sniper/", "Sniper lab"], whales: ["whales/", "Whale tracker"], predict: ["predict/", "Prediction markets"], ict: ["ict/", "ICT setups"], dex: ["dex/", "DEX tokens"] };
  const open = (p, tab) => `<a class="ask-open" href="${PAGE[p][0]}${tab ? `#/${tab}` : ""}">Open ${esc(PAGE[p][1])} →</a>`;
  const NOTE = '<p class="ask-note">Paper trading and education only: a tested idea can still lose. Always use the safety exit and the time limit.</p>';

  // ------------------------------------------------------------------ ideas from each market, in one shape
  function gradePill(g, rank, off) {
    if (off) return `<span class="pill calm">Ranked #${rank}</span>`;
    const c = g === "Strong" ? "good" : g === "Moderate" ? "calm" : g === "Weak" ? "warn" : "bad";
    return `<span class="pill ${c}">${esc(g)}</span>`;
  }
  async function ideasFor(m) {
    if (m === "main" || m === "small") {
      const [s, b] = await Promise.all([get(m), get(m === "main" ? "mainBoard" : "smallBoard")]); if (!s) return null;
      const off = unreliable(b?.quality);
      return { m, at: s.generated_at, off, mood: s.market_mood, items: (s.ideas || []).map((d, i) => ({
        name: d.coin, side: "BUY", grade: d.grade, score: d.score, rank: d.rank || i + 1, price: d.price_now, tp: d.take_profit, sl: d.safety_exit,
        by: Date.parse(d.exit_by), why: d.why || [], warn: d.warnings || [], risk: d.risk_level, extra: `${Math.round((d.chance_beats_market || 0.5) * 100)}% chance it beats the market` })) };
    }
    if (m === "stocks") {
      const s = await get("stocks"); if (!s) return null;
      const list = s.lists?.large ? "large" : Object.keys(s.lists || {})[0]; const style = Object.keys(s.lists?.[list] || {})[0];
      const rows = (s.lists?.[list]?.[style] || []).slice().sort((a, b) => b.score - a.score).slice(0, 5);
      return { m, at: s.generated_at, off: unreliable(s.quality?.[list]), closed: s.market && !s.market.open, items: rows.map((d, i) => ({
        name: `${d.symbol}`, sub: d.name, side: "BUY", grade: d.grade, score: d.score, rank: i + 1, price: d.price, tp: d.price * (1 + d.take_profit_pct), sl: d.price * (1 + d.safety_exit_pct),
        why: d.why || [], warn: d.warnings || [], extra: s.styles?.[style]?.label || "" })) };
    }
    if (m === "fx") {
      const s = await get("fx"); if (!s) return null;
      const rows = (s.lists?.main?.fx_today || []).filter((d) => !(d.hard || []).length).slice().sort((a, b) => b.score - a.score).slice(0, 5);
      return { m, at: s.generated_at, off: unreliable(s.quality), closed: s.open === false, items: rows.map((d, i) => ({
        name: d.label, side: d.side.toUpperCase(), grade: d.grade, score: d.score, rank: i + 1, price: d.price, tp: d.take_profit, sl: d.safety_exit, by: Date.parse(d.sell_by),
        why: d.why || [], warn: d.warnings || [], extra: d.headline })) };
    }
    if (m === "lab") {
      const s = await get("lab"); if (!s) return null;
      return { m, at: s.generated_at, off: unreliable(s.quality), items: (s.ideas || []).slice(0, 5).map((d, i) => ({
        name: d.label, sub: d.name, side: d.side > 0 ? "BUY" : "SELL", grade: d.grade, score: d.score, rank: d.rank || i + 1, price: d.last, tp: d.target, sl: d.stop,
        why: [d.exit_rule].filter(Boolean), warn: d.warnings || [], extra: d.evidence })) };
    }
    return null;
  }
  function ideaCard(d, off) {
    const why = (Array.isArray(d.why) ? d.why : [d.why]).filter(Boolean).slice(0, 2).map((w) => `<li>${esc(typeof w === "string" ? w : w.text || "")}</li>`).join("");
    const warn = (Array.isArray(d.warn) ? d.warn : [d.warn]).filter(Boolean).slice(0, 1).map((w) => `<li class="w">${esc(typeof w === "string" ? w : w.text || "")}</li>`).join("");
    return `<div class="ask-idea"><div class="ask-idea-h"><b>${esc(d.side)} ${esc(d.name)}</b>${d.sub ? `<span class="muted small"> ${esc(d.sub)}</span>` : ""}
      <span class="ask-idea-g">${fin(d.score) ? `<span class="num">${d.score.toFixed(1)}/10</span>` : ""} ${gradePill(d.grade, d.rank, off)}</span></div>
      <div class="ask-facts"><span>Price <b class="num">${px(d.price)}</b></span>${fin(d.tp) ? `<span>Take profit <b class="num up">${px(d.tp)}</b></span>` : ""}${fin(d.sl) ? `<span>Safety exit <b class="num down">${px(d.sl)}</b></span>` : ""}${fin(d.by) ? `<span>Sell by <b>${when(d.by)}</b></span>` : ""}</div>
      ${d.extra ? `<div class="small muted">${esc(d.extra)}</div>` : ""}${why || warn ? `<ul class="ask-why">${why}${warn}</ul>` : ""}</div>`;
  }
  function verdictLine(set) {
    const top = set.items[0];
    if (!top) return "There are no ideas on this page right now.";
    if (set.off) return "Grades are <b>switched off</b> on this page right now (the latest check showed higher grades did no better than lower ones), so treat these as practice, in score order.";
    if (top.grade === "Strong" || top.grade === "Moderate") return `The best one right now is graded <b>${esc(top.grade)}</b>.`;
    return "<b>Nothing here is worth trading right now</b>: even the best idea is graded " + esc(top.grade) + ". Doing nothing is a perfectly good choice.";
  }
  async function marketAnswer(m, n = 3) {
    const set = await ideasFor(m); if (!set) return dataDown(m);
    const closed = set.closed ? ` <span class="pill warn">market closed: these can only be acted on when it opens</span>` : "";
    return `<p><b>${esc(PAGE[m][1])}</b>, updated ${esc(ago(set.at))}.${closed} ${verdictLine(set)}</p>${set.items.slice(0, n).map((d) => ideaCard(d, set.off)).join("")}${open(m, m === "lab" ? "" : "ideas")}${NOTE}`;
  }
  const dataDown = (m) => `<p>I couldn't load the latest ${esc(PAGE[m][1])} data just now. Try again in a minute, or ${open(m)}</p>`;

  // ------------------------------------------------------------------ answers
  async function bestOverall() {
    const sets = (await Promise.all(["main", "stocks", "fx", "lab"].map(ideasFor))).filter(Boolean);
    const all = sets.flatMap((s) => s.items.slice(0, 1).map((d) => ({ d, s }))).filter((x) => !x.s.closed);
    const usable = all.filter((x) => !x.s.off && (x.d.grade === "Strong" || x.d.grade === "Moderate")).sort((a, b) => b.d.score - a.d.score);
    if (!usable.length) {
      return `<p><b>Nothing across the markets is graded Strong or Moderate right now</b> (or grades are switched off where they aren't reliable). That's normal: on most days doing nothing is the right call.</p>
        <p>The highest-scoring ideas anyway, to learn from:</p>${all.sort((a, b) => b.d.score - a.d.score).slice(0, 3).map((x) => `<div class="ask-src">${esc(PAGE[x.s.m][1])}</div>${ideaCard(x.d, x.s.off)}`).join("")}${NOTE}`;
    }
    return `<p>The best-graded ideas across the markets right now:</p>${usable.slice(0, 3).map((x) => `<div class="ask-src">${esc(PAGE[x.s.m][1])} · ${esc(ago(x.s.at))} ${open(x.s.m, x.s.m === "lab" ? "" : "ideas")}</div>${ideaCard(x.d, false)}`).join("")}${NOTE}`;
  }
  const ALIAS = { BITCOIN: "BTC", ETHEREUM: "ETH", ETHER: "ETH", SOLANA: "SOL", RIPPLE: "XRP", DOGECOIN: "DOGE", CARDANO: "ADA", GOLD: "XAU", SILVER: "XAG", APPLE: "AAPL", TESLA: "TSLA", NVIDIA: "NVDA", MICROSOFT: "MSFT", AMAZON: "AMZN", EURO: "EUR", POUND: "GBP", YEN: "JPY" };
  const STOP = new Set(("I A AN THE IS IT TO OF IN ON AT FOR AND OR BUY SELL NOW TODAY GOOD BAD SHOULD WHAT WHATS HOW WHY WHO ME MY BE DO DOES CAN ABOUT " +
    "TRADE TRADES IDEA IDEAS BEST TOP ANY THIS THAT WITH ARE WAS WILL HOLD LONG SHORT PRICE OK STOCK STOCKS CRYPTO COIN COINS FOREX MARKET MARKETS " +
    "RIGHT WHICH THERE GIVE SHOW TELL PLEASE WANT THINK SOME LOOK LOOKING GOING UP DOWN NEXT WEEK DAY TOMORROW YES NO").split(" "));
  // a word is looked up as a symbol when typed in capitals (SOL, NVDA), written as a pair (EUR/USD) or a known name (silver, bitcoin)
  function symbolsIn(q) {
    const words = q.replace(/[^A-Za-z0-9/ ]/g, " ").split(/\s+/).filter(Boolean);
    const out = [];
    for (const w of words) {
      const up = w.toUpperCase(); const a = ALIAS[up];
      const sym = a || up.replace("/", "");
      if (STOP.has(up) || sym.length < 2 || sym.length > 10) continue;
      if (a || w.includes("/") || (w === up && /[A-Z]/.test(w))) out.push(sym);
    }
    return [...new Set(out)];
  }
  async function aboutSymbols(syms) {
    const [main, small, stocks, fx, whales, lab, listings] = await Promise.all([ideasFor("main"), ideasFor("small"), get("stocks"), get("fx"), get("whales"), ideasFor("lab"), get("listings")]);
    const out = [];
    for (const sym of syms.slice(0, 3)) {
      const parts = [];
      for (const set of [main, small].filter(Boolean)) {
        const d = set.items.find((x) => x.name === sym);
        if (d) parts.push(`<div class="ask-src">${esc(PAGE[set.m][1])} · ${esc(ago(set.at))} ${open(set.m, "ideas")}</div>${ideaCard(d, set.off)}`);
      }
      for (const [list, styles] of Object.entries(stocks?.lists || {})) {
        const rows = Object.values(styles).flat().filter((r) => r.symbol === sym);
        if (rows.length) { const d = rows.sort((a, b) => b.score - a.score)[0]; parts.push(`<div class="ask-src">US stocks · ${esc(ago(stocks.generated_at))} ${open("stocks", "ideas")}</div>${ideaCard({ name: d.symbol, sub: d.name, side: "BUY", grade: d.grade, score: d.score, rank: 1, price: d.price, tp: d.price * (1 + d.take_profit_pct), sl: d.price * (1 + d.safety_exit_pct), why: d.why, warn: d.warnings }, unreliable(stocks.quality?.[list]))}`); break; }
      }
      const fxRows = Object.values(fx?.lists || {}).flatMap((by) => Object.values(by).flat()).filter((r) => r.pair.includes(sym) && !(r.hard || []).length);
      if (fxRows.length) { const d = fxRows.sort((a, b) => b.score - a.score)[0]; parts.push(`<div class="ask-src">Forex & gold · ${esc(ago(fx.generated_at))} ${open("fx", "ideas")}</div>${ideaCard({ name: d.label, side: d.side.toUpperCase(), grade: d.grade, score: d.score, rank: 1, price: d.price, tp: d.take_profit, sl: d.safety_exit, by: Date.parse(d.sell_by), why: d.why, warn: d.warnings, extra: d.headline }, unreliable(fx.quality))}`); }
      const ld = lab?.items.find((x) => (x.name || "").toUpperCase().includes(sym)); if (ld) parts.push(`<div class="ask-src">Strategy lab ${open("lab")}</div>${ideaCard(ld, lab.off)}`);
      const w = (whales?.consensus || []).find((c) => c.coin === sym);
      if (w) {
        const sg = w.signals; const lean = sg ? (sg.lean > 0 ? "Up" : sg.lean < 0 ? "Down" : "no clear lean") : "not enough history";
        parts.push(`<div class="ask-src">Whale tracker ${open("whales")}</div><p>${w.long_n} of the followed whales are buying ${esc(sym)} and ${w.short_n} betting against it (${usdM(w.long_usd)} long, ${usdM(w.short_usd)} short). Outlook: <b>${lean}</b> <span class="pill warn">Not proven</span>. Copying whale positions is still being tested: information only.</p>`);
      }
      const li = (listings?.listings || []).find((r) => r.base === sym);
      if (li) parts.push(`<div class="ask-src">New listings ${open("listings")}</div><p>${esc(sym)} was listed ${Math.round(li.age_d)} day(s) ago: ${pct(li.chg)} since its first price, ${pct(li.from_high)} from its high.</p>`);
      if (parts.length && !parts.some((x) => x.includes("ask-idea"))) parts.unshift(`<p>${esc(sym)} is <b>not among today's trade ideas</b> on any page: it doesn't score high enough right now. Here's what else we know:</p>`);
      out.push(parts.length ? `<h4>${esc(sym === "XAU" ? "Gold" : sym === "XAG" ? "Silver" : sym)}</h4>${parts.join("")}`
        : `<h4>${esc(sym)}</h4><p>${esc(sym)} isn't among today's ideas on any page, and the whales we follow don't hold it. That usually means it doesn't score well right now (or isn't covered). Ask me for "the best ideas" instead.</p>`);
    }
    return out.join("") + NOTE;
  }
  // every symbol in today's data, so "is sol a buy" finds SOL without capitals
  async function known() {
    const [m, sm, st, fx, wh] = await Promise.all([get("main"), get("small"), get("stocks"), get("fx"), get("whales")]);
    const set = new Set();
    for (const d of [...(m?.ideas || []), ...(sm?.ideas || [])]) set.add(d.coin);
    for (const by of Object.values(st?.lists || {})) for (const r of Object.values(by).flat()) set.add(r.symbol);
    for (const by of Object.values(fx?.lists || {})) for (const r of Object.values(by).flat()) { set.add(r.pair.slice(0, 3)); set.add(r.pair.slice(3)); }
    for (const c of wh?.consensus || []) set.add(c.coin);
    set.delete("USD"); return set;
  }
  async function predictAnswer() {
    const s = await get("predict"); if (!s) return dataDown("predict");
    const r = s.record || {}; const p = (s.picks || []).slice(0, 4);
    return `<p><b>Prediction markets</b> (Kalshi and Polymarket, paper only, checked ${esc(ago(s.generated_at))}). The one rule that looked promising in our research: buy the favourite at 55-70¢, 1-7 days before the market ends. <b>${esc(r.label || "Not proven yet")}</b>: ${esc(r.text || "")}</p>
      ${p.length ? `<div class="table-wrap"><table><thead><tr><th>Market</th><th>Buy</th><th class="num">Price</th></tr></thead><tbody>${p.map((x) => `<tr><td style="white-space:normal">${esc(x.q)}</td><td>${esc(x.side.toUpperCase())}</td><td class="num">${Math.round(x.ask * 100)}¢</td></tr>`).join("")}</tbody></table></div>` : "<p>No market fits the rule right now.</p>"}
      ${open("predict")}<p class="ask-note">A contract pays $1 if right and nothing if wrong. Kalshi is regulated in the US; Polymarket's main site isn't open to US residents.</p>`;
  }
  async function whalesAnswer() {
    const s = await get("whales"); if (!s) return dataDown("whales");
    const v = s.verdict || {}; const c = (s.consensus || []).slice(0, 6);
    return `<p><b>What last month's best Hyperliquid traders hold now</b> (checked ${esc(ago(s.generated_at))}):</p>
      <div class="table-wrap"><table><thead><tr><th>Coin</th><th class="num">Buying / betting against</th><th>Outlook</th></tr></thead><tbody>${c.map((x) => `<tr><td><b>${esc(x.coin)}</b></td><td class="num"><span class="up">${x.long_n}</span> / <span class="down">${x.short_n}</span></td><td>${x.signals ? (x.signals.lean > 0 ? "Up" : x.signals.lean < 0 ? "Down" : "no clear lean") : "–"}</td></tr>`).join("")}</tbody></table></div>
      <p>The paper copy of these whales: <b>${pct(v.lead)}</b> so far, against ${pct(v.rand)} for random whales and ${pct(v.btc)} for just holding Bitcoin. ${esc(v.text || "")}</p>${open("whales")}
      <p class="ask-note">Information only until the 30-day verdict: copying single whale trades didn't pay in our tests. Hyperliquid isn't available to US residents.</p>`;
  }
  async function moodAnswer() {
    const [m, st, fx] = await Promise.all([get("main"), get("stocks"), get("fx")]);
    const parts = [];
    if (m?.market_mood) parts.push(`<li><b>Crypto:</b> ${esc(m.market_mood.label)}: ${Math.round(m.market_mood.share_positive * 100)}% of ${m.coins_scanned} coins look positive after costs for the next 4 hours.</li>`);
    if (st?.market) parts.push(`<li><b>US stocks:</b> the market is ${st.market.open ? "open" : "closed"}.</li>`);
    if (fx) parts.push(`<li><b>Forex:</b> ${fx.open ? "open" : "closed (weekend)"}${(fx.fix_windows || [])[0] ? `; next rate-fixing window: ${esc(fx.fix_windows[0].name)} at ${when(Date.parse(fx.fix_windows[0].start))} (don't trade inside it)` : ""}.</li>`);
    return `<p>The mood right now:</p><ul>${parts.join("")}</ul><p class="small muted">"Unfavourable" means few ideas beat their costs: a day to be patient.</p>${open("main", "ideas")}`;
  }
  async function recordAnswer() {
    const [m, st, fx, lab, wh] = await Promise.all([get("main"), get("stocks"), get("fx"), get("lab"), get("whales")]);
    const row = (name, q) => { const s = (q?.by_grade || []).find((g) => g.grade === "Strong"); return s ? `<li><b>${name}</b>: Strong ideas ended in profit ${Math.round(s.win_rate * 100)}% of the time, average ${pct(s.avg_return, 2)} after costs, against ${pct(s.random_avg_return, 2)} for random picks (${s.ideas} ideas).</li>` : ""; };
    const [mb] = await Promise.all([get("mainBoard")]);
    const mm = m?.model;
    return `<p>How the ideas really did, after costs, compared with random picks:</p><ul>
      ${row("Exchange coins", mb?.quality) || (mm ? `<li><b>Exchange coins</b> (test period): the top 5 ideas made money ${Math.round(mm.top5_win_rate * 100)}% of the time, average ${pct(mm.top5_avg_net_ret, 2)} per idea after costs.</li>` : "")}
      ${row("US stocks", st?.quality?.large)}${row("Forex & gold", fx?.quality)}
      ${lab ? (() => { const all = Object.values(lab.strategies || {}).filter((x) => x.stats); return `<li><b>Strategy lab</b>: ${all.filter((x) => x.stats.level === "good").length} of ${all.length} famous strategies held up in the re-test.</li>`; })() : ""}
      ${wh?.verdict ? `<li><b>Whale copy</b>: ${pct(wh.verdict.lead)} so far (${wh.verdict.days ?? 0} day(s); verdict after 30).</li>` : ""}</ul>
      <p>Honest summary: <b>no page has proven a reliable edge yet</b>. Grades switch themselves off where they stop working, and every page keeps its full record. Each page's "Track record" tab has the details.</p>`;
  }
  function explainAnswer(q) {
    const terms = (root.OmegaExplain && root.OmegaExplain.terms) || [];
    const t = q.toLowerCase().replace(/^(what|whats|what's)\s+(is|are|does)\s+|^(explain|define|meaning of|what does)\s+|\?|\bmean\b/g, " ").replace(/\s+/g, " ").trim();
    // an exact name first, then the longest term named in the question, then a term the question is part of
    const all = terms.flatMap((e) => [e.t.toLowerCase(), ...(e.keys || [])].map((k) => [k.replace(/^(th|seg):/, ""), e])).filter(([k]) => k.length > 2);
    const hit = all.find(([k]) => k === t) || all.filter(([k]) => t.includes(k)).sort((a, b) => b[0].length - a[0].length)[0]
      || all.filter(([k]) => k.includes(t) && t.length > 3).sort((a, b) => a[0].length - b[0].length)[0];
    const best = hit ? hit[1] : null;
    if (!best) return null;
    return `<p><b>${esc(best.t)}</b>: ${esc(best.w)}</p>${best.d ? `<div class="xp-do"><span>What to do</span>${esc(best.d)}</div>` : ""}`;
  }
  function helpAnswer() {
    return `<p>I answer from the site's live, tested data. Try:</p><ul>
      <li><b>"What's the best trade right now?"</b> across crypto, stocks, forex and the strategy lab</li>
      <li><b>A name</b>: "SOL", "silver", "NVDA", "EUR/USD"</li>
      <li><b>A market</b>: "crypto ideas", "stock ideas", "forex", "small coins", "new listings"</li>
      <li><b>"What are the whales holding?"</b>, <b>"How's the market?"</b>, <b>"How did the ideas do?"</b></li>
      <li><b>"What is a safety exit?"</b> or any term you see on the site</li></ul>
      <p class="small muted">Some pages work live in your browser and aren't summarised here: ${open("ict")} · ${open("dex")}</p>`;
  }

  // ------------------------------------------------------------------ understanding the question
  async function answer(q) {
    const s = q.toLowerCase();
    if (/^(hi|hello|hey|help|what can you do|\?)\b/.test(s.trim()) || s.trim().length < 2) return helpAnswer();
    if (/whale|big traders/.test(s)) return whalesAnswer();
    if (/predict|kalshi|polymarket|\bbet(s|ting)?\b|odds/.test(s)) return predictAnswer();
    if (/^(what|whats|what's)\s+(is|are|does)\b|^(explain|define|meaning)\b|\bwhat does .* mean/.test(s) && !/(best|hold|market|mood|record|idea|trade|buy|sell)/.test(s)) { const e = explainAnswer(q); if (e) return e; }
    if (/\b(mood|market (like|today|now)|how'?s the market|how is the market)\b/.test(s)) return moodAnswer();
    if (/\b(record|did .* do|results|performance|track|proven|work(ed|s)?\b.*\?)/.test(s)) return recordAnswer();
    if (/\b(new listing|listing|just listed|new coin)/.test(s)) return listingsAnswer();
    if (/\b(snipe|sniper|new token|launch)/.test(s)) return sniperAnswer();
    if (/\bict\b/.test(s)) return `<p>ICT setups are worked out live in your browser from the charts, so I can't summarise them here.</p>${open("ict", "setups")}`;
    if (/\b(dex|defi|on-?chain|meme ?coin)/.test(s)) return `<p>DEX tokens are scam-checked and scored live in your browser, so open the page to see them.</p>${open("dex", "ideas")}`;
    if (/\bsmall (coin|cap)/.test(s)) return marketAnswer("small");
    if (/\b(stocks?|shares?|equit\w*|nasdaq|s&p|dow)\b/.test(s) && !symbolsIn(q).some((x) => x !== "US")) return marketAnswer("stocks");
    if (/\b(forex|fx|currenc|pairs?)\b/.test(s)) return marketAnswer("fx");
    if (/\b(strateg|lab|turtle|rsi)\b/.test(s)) return marketAnswer("lab");
    if (/\b(crypto|coins?)\b/.test(s) && !symbolsIn(q).length) return marketAnswer("main");
    const syms = symbolsIn(q);
    if (!syms.length) for (const w of q.toUpperCase().replace(/[^A-Z0-9 ]/g, " ").split(/\s+/)) if (w.length >= 2 && !STOP.has(w) && (await known()).has(w)) syms.push(w);
    if (/\b(best|top|good|what should|recommend|opportunit|idea|trade)\b/.test(s) && !syms.length) return bestOverall();
    if (syms.length) return aboutSymbols(syms);
    const e = explainAnswer(q); if (e) return e;
    return `<p>I'm not sure what you mean. I can find trade ideas, look up a coin, stock or currency, tell you what the whales hold, or explain any term.</p>${helpAnswer()}`;
  }
  async function listingsAnswer() {
    const s = await get("listings"); if (!s) return dataDown("listings");
    const recent = (s.listings || []).slice().sort((a, b) => a.age_d - b.age_d).slice(0, 5);
    const ideas = s.ideas || [];
    return `<p><b>New listings</b> (updated ${esc(ago(s.generated_at))}): ${ideas.length ? `${ideas.length} tested idea(s) right now.` : "<b>no tested rule is firing right now</b>, so there's nothing to act on."}</p>
      <div class="table-wrap"><table><thead><tr><th>Coin</th><th>Exchange</th><th class="num">Listed</th><th class="num">Since first price</th></tr></thead><tbody>${recent.map((r) => `<tr><td><b>${esc(r.base)}</b></td><td>${esc(r.exchange)}</td><td class="num">${r.age_d < 1 ? `${Math.round(r.age_d * 24)} h ago` : `${Math.round(r.age_d)} d ago`}</td><td class="num ${r.chg >= 0 ? "up" : "down"}">${pct(r.chg)}</td></tr>`).join("")}</tbody></table></div>
      <p class="small muted">Most new listings fall after the first days: in our tests, buying in the first hour lost money on average.</p>${open("listings")}`;
  }
  async function sniperAnswer() {
    const s = await get("sniper"); if (!s) return dataDown("sniper");
    const c = s.counts || {};
    return `<p><b>Sniper lab</b> (paper only, updated ${esc(ago(s.generated_at))}): it checks brand-new DEX tokens for scams and paper-buys the ones that pass, to test whether sniping pays after the bots and the costs.</p>
      <p>${Object.entries(c).slice(0, 4).map(([k, v]) => `${esc(k)}: <b>${esc(v)}</b>`).join(" · ")}</p>
      <p>${s.proven ? "The current rules have held up in paper tests." : `<b>Not proven:</b> 147 of the first 152 coins that passed the earlier, looser checks were rug-pulled within 12 hours. The stricter rules (locked pool money and a proven test sale) need ${esc(s.proof_n || 50)} passed paper snipes before anything here raises an alert.`} The Sniper lab is never part of "the best ideas".</p>
      <p>No real-money sniping until the results prove it works.</p>${open("sniper")}`;
  }

  // ------------------------------------------------------------------ the chat
  const HIST = "omega.ask.history";
  function bubble(role, html, at) {
    const el = document.createElement("div"); el.className = `ask-msg ${role}`;
    el.innerHTML = role === "user" ? `<div class="ask-b">${esc(html)}</div>` : `<div class="ask-av" aria-hidden="true">Ω</div><div class="ask-b">${html}${at ? `<div class="ask-at">${esc(when(at))}</div>` : ""}</div>`;
    $("thread").appendChild(el); return el;
  }
  function started() { document.body.classList.add("ask-started"); }
  async function send(q) {
    q = String(q || "").trim(); if (!q) return;
    started(); bubble("user", q);
    const wait = bubble("bot", '<span class="ask-typing"><i></i><i></i><i></i></span> <span class="muted small">Checking the latest data…</span>');
    wait.scrollIntoView({ block: "end", behavior: "smooth" });
    let html; try { html = await answer(q); } catch { html = "<p>Something went wrong reading the data. Please try again in a minute.</p>"; }
    const at = Date.now();
    wait.querySelector(".ask-b").innerHTML = `${html}<div class="ask-at">${esc(when(at))}</div>`;
    const h = store.get(HIST, []); h.push({ q, a: html, at }); store.set(HIST, h.slice(-30));
    wait.scrollIntoView({ block: "start", behavior: "smooth" });
    root.dispatchEvent(new CustomEvent("omega:asked", { detail: { count: h.length } }));
  }
  function restore() {
    const h = store.get(HIST, []); if (!h.length) return;
    started();
    const d = document.createElement("div"); d.className = "ask-divider";
    d.innerHTML = `<span>Earlier (answers as they were then)</span><button type="button" class="xp-hide" data-clear>Clear history</button>`;
    $("thread").appendChild(d);
    for (const x of h.slice(-10)) { bubble("user", x.q); bubble("bot", x.a, x.at); }
    const n = document.createElement("div"); n.className = "ask-divider"; n.innerHTML = "<span>Now</span>"; $("thread").appendChild(n);
  }
  function start() {
    const form = $("askForm"); const input = $("askInput");
    form.addEventListener("submit", (e) => { e.preventDefault(); const q = input.value; input.value = ""; input.style.height = ""; send(q); });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
    input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 160)}px`; });
    document.addEventListener("click", (e) => {
      const c = e.target.closest("[data-ask]"); if (c) { e.preventDefault(); send(c.dataset.ask); return; }
      if (e.target.closest("[data-clear]")) { store.set(HIST, []); $("thread").innerHTML = ""; document.body.classList.remove("ask-started"); }
    });
    restore();
    const q = new URLSearchParams(location.search).get("q"); if (q) send(q);
    else if (!store.get(HIST, []).length) input.focus({ preventScroll: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
  root.OmegaAsk = { answer, send };
})(window);
