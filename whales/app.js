/* Whale tracker (paper only): the paper mirror of last month's best Hyperliquid whales against random whales and
   BTC, what they hold now (consensus per coin), what just changed, and who they are. Reads whales/run.py's snapshot
   (every 15 minutes). Prices are the ones at the last check, labelled; an old snapshot hides the live parts. */
(() => {
  "use strict";
  const DATA = window.OMEGA_WHALES_DATA || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/data/whales/";
  const STALE_MIN = 45;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fin = (x) => typeof x === "number" && Number.isFinite(x);
  const pct = (x, d = 1) => (fin(x) ? `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(d)}%` : "–");
  const pc0 = (x) => (fin(x) ? `${Math.round(x * 100)}%` : "–");
  const cls = (x) => (fin(x) ? (x > 0 ? "up" : x < 0 ? "down" : "") : "");
  const usd = (x) => (!fin(x) ? "–" : Math.abs(x) >= 1e6 ? `$${(x / 1e6).toFixed(1)}M` : `$${Math.round(x / 1e3)}k`);
  const px = (x) => (fin(x) ? (x >= 100 ? x.toLocaleString(undefined, { maximumFractionDigits: 1 }) : x >= 1 ? x.toFixed(3) : x.toPrecision(4)) : "–");
  const when = (t) => new Date(t).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const day = (t) => new Date(t).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
  const short = (a, n) => `${n ? esc(n) + " · " : ""}<span class="addr">${esc(a.slice(0, 6))}…${esc(a.slice(-4))}</span>`;
  const link = (a, n) => `<a href="https://app.hyperliquid.xyz/explorer/address/${esc(a)}" target="_blank" rel="noopener">${short(a, n)}</a>`;
  const LV = { good: ["Held up", "good"], warn: ["Not proven yet", "warn"], bad: ["Failed", "bad"] };
  let stale = true;

  function summary(S) {
    const v = S.verdict || {}; const c = S.cohort || {};
    $("summary").className = `banner ${v.level === "good" ? "good" : v.level === "bad" ? "bad" : "calm"}`;
    $("summary").innerHTML = `<b>Paper only.</b> Every 30 days we pick the 20 big traders (accounts of $1M+, not trading bots) with the best results on Hyperliquid over the previous month, and copy their <b>whole positions</b> on paper every 15 minutes, after real costs and funding, next to a copy of 20 random whales and plain BTC.
      This cohort started ${c.start ? day(c.start) : "–"} (chosen from ${c.eligible ?? "–"} eligible whales). <b>Verdict: ${esc(LV[v.level || "warn"][0])}.</b> ${esc(v.text || "")}`;
  }
  function chart(curve) {
    if (!curve || curve.length < 2) return `<p class="small muted">The chart appears after a few checks.</p>`;
    const W = 640, H = 200, P = 28;
    const xs = curve.map((r) => r[0]); const ys = curve.flatMap((r) => [r[1], r[2], r[3]]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys, 1) * 0.995, y1 = Math.max(...ys, 1) * 1.005;
    const X = (t) => P + ((t - x0) / Math.max(x1 - x0, 1)) * (W - 2 * P); const Y = (v) => H - P - ((v - y0) / Math.max(y1 - y0, 1e-9)) * (H - 2 * P);
    const line = (k, col, dash) => `<polyline fill="none" stroke="${col}" stroke-width="2" ${dash ? 'stroke-dasharray="5 4"' : ""} points="${curve.map((r) => `${X(r[0]).toFixed(1)},${Y(r[k]).toFixed(1)}`).join(" ")}"/>`;
    return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Paper mirror against random whales and BTC" style="max-width:100%;height:auto">
      <line x1="${P}" x2="${W - P}" y1="${Y(1)}" y2="${Y(1)}" stroke="var(--line-strong)" stroke-dasharray="2 4"/>
      ${line(3, "var(--muted)", true)}${line(2, "var(--warn)")}${line(1, "var(--accent)")}
      <text x="${P}" y="${P - 10}">${pct(y1 - 1)}</text><text x="${P}" y="${H - 8}">${pct(y0 - 1)}</text></svg>
      <div class="chart-legend"><span><i style="border-color:var(--accent)"></i>Copying last month's best whales</span><span><i style="border-color:var(--warn)"></i>Copying random whales</span><span><i style="border-color:var(--muted);border-top-style:dashed"></i>Holding BTC</span></div>`;
  }
  function mirror(S) {
    const m = S.mirror || {}; const v = S.verdict || {}; const last = (m.curve || []).slice(-1)[0];
    $("mirror").innerHTML = `<div class="stats">
        <div class="stat"><div class="k">Best whales, copied</div><div class="v ${cls(v.lead)}">${pct(v.lead, 2)}</div><div class="small muted">since the mirror started</div></div>
        <div class="stat"><div class="k">Random whales, copied</div><div class="v ${cls(v.rand)}">${pct(v.rand, 2)}</div></div>
        <div class="stat"><div class="k">Just holding BTC</div><div class="v ${cls(v.btc)}">${pct(v.btc, 2)}</div></div>
        <div class="stat"><div class="k">Days of results</div><div class="v">${v.days ?? 0}</div><div class="small muted">verdict after 30</div></div></div>
      ${chart(m.curve)}
      <p class="small muted">The mirror holds the whales' positions in proportion to each one's account (their own leverage, ${(S.costs || {}).max_gross || 5}x at most in total; now ${(m.gross_lead || 0).toFixed(1)}x), re-copied every 15 minutes, paying ${(((S.costs || {}).side || 0) * 100).toFixed(2)}% per side on every change plus funding. ${last ? `Last update ${when(last[0])}.` : ""}</p>
      ${(m.exp || []).length ? `<details><summary>What the mirror holds now</summary><div class="table-wrap"><table><thead><tr><th>Coin</th><th>Side</th><th class="num">Size, per $1 of paper money</th></tr></thead><tbody>${m.exp.map(([c, e]) => `<tr><td><b>${esc(c)}</b></td><td>${e > 0 ? '<span class="up">long</span>' : '<span class="down">short</span>'}</td><td class="num">$${Math.abs(e).toFixed(2)}</td></tr>`).join("")}</tbody></table></div></details>` : ""}
      ${(S.history || []).length > 1 ? `<details><summary>Each monthly cohort</summary><div class="table-wrap"><table><thead><tr><th>Cohort</th><th class="num">Days</th><th class="num">Best whales copied</th><th class="num">Random whales</th><th class="num">BTC</th></tr></thead><tbody>${S.history.map((h) => `<tr><td>${esc(h.id)}</td><td class="num">${h.days.toFixed(0)}</td><td class="num ${cls(h.lead)}">${pct(h.lead)}</td><td class="num ${cls(h.rand)}">${pct(h.rand)}</td><td class="num ${cls(h.btc)}">${pct(h.btc)}</td></tr>`).join("")}</tbody></table></div></details>` : ""}`;
  }
  function consensus(S) {
    if (stale) { $("consensus").innerHTML = `<div class="banner warn">The latest check is more than ${STALE_MIN} minutes old, so live positions are hidden.</div>`; return; }
    const c = S.consensus || []; const p = S.positions || [];
    $("consensus").innerHTML = !c.length ? `<p class="muted">The followed whales hold no positions right now.</p>` : `<div class="table-wrap"><table><thead><tr><th>Coin</th><th class="num">Price</th><th class="num">Whales long / short</th><th class="num">Long $</th><th class="num">Short $</th><th>Leaning</th><th>Market signals</th><th>Outlook</th></tr></thead><tbody>
      ${c.map((x) => `<tr><td><b>${esc(x.coin)}</b></td><td class="num">${px(x.price)}</td><td class="num"><span class="up">${x.long_n}</span> / <span class="down">${x.short_n}</span></td><td class="num">${usd(x.long_usd)}</td><td class="num">${usd(x.short_usd)}</td><td>${x.net_usd > 0 ? '<span class="pill good">long</span>' : x.net_usd < 0 ? '<span class="pill bad">short</span>' : "even"}</td>${signalCells(x, S.outlook)}</tr>`).join("")}
      </tbody></table></div>${outlookNote(S.outlook)}
      <details><summary>Every position (${p.length})</summary><div class="table-wrap"><table><thead><tr><th>Whale</th><th>Coin</th><th>Side</th><th class="num">Size</th><th class="num">Entry price</th><th class="num">Price now</th><th class="num">Profit so far</th><th class="num">Leverage</th></tr></thead><tbody>
      ${p.map((x) => `<tr><td class="small">${link(x.addr, x.name)}</td><td><b>${esc(x.coin)}</b></td><td>${x.side === "long" ? '<span class="up">long</span>' : '<span class="down">short</span>'}</td><td class="num">${usd(x.notional)}</td><td class="num">${px(x.entry)}</td><td class="num">${px(x.price)}</td><td class="num ${cls(x.upnl)}">${usd(x.upnl)}</td><td class="num">${fin(x.lev) ? x.lev.toFixed(0) + "x" : "–"}</td></tr>`).join("")}
      </tbody></table></div></details><p class="small muted">Prices at ${S.generated_at ? when(Date.parse(S.generated_at)) : "the last check"}, not live. A whale's position can be one leg of a hedge held elsewhere.</p>`;
  }
  // market signals and the outlook on probation (whales/outlook.py): facts first, the lean labelled with its evidence
  function signalCells(x, rec) {
    const g = x.signals;
    if (!g) return '<td class="small muted">not enough history</td><td></td>';
    const arrow = (v) => (v > 0 ? '<span class="up">up</span>' : '<span class="down">down</span>');
    const crowd = g.crowd < 0 ? "many betting up" : g.crowd > 0 ? "many betting down" : "normal";
    const whales = x.net_usd > 0 ? 1 : x.net_usd < 0 ? -1 : 0;
    const lean = g.lean > 0 ? '<span class="pill good">Up</span>' : g.lean < 0 ? '<span class="pill bad">Down</span>' : '<span class="pill calm">No clear lean</span>';
    const agree = !g.lean || !whales ? "" : g.lean === whales ? '<div class="small muted">agrees with the whales</div>' : '<div class="small muted">disagrees with the whales</div>';
    const label = rec && rec.label ? rec.label : "Not proven";
    return `<td class="small">Trend ${arrow(g.trend)} · Momentum ${arrow(g.mom)}<div class="muted">Crowding: ${crowd}</div></td>
      <td>${lean} <span class="pill ${label === "Held up" ? "good" : label === "Failed" ? "bad" : "warn"}">${esc(label)}</span>${agree}</td>`;
  }
  function outlookNote(rec) {
    if (!rec) return "";
    const bt = rec.backtest || {};
    const live = rec.judged ? ` Live so far: ${rec.judged} call(s) judged after ${rec.hold_days} days, right ${Math.round(rec.hit * 100)}% of the time, average ${pct(rec.avg)} after costs.` : ` Live record: every up or down call is checked ${rec.hold_days || 7} days later; the first results come in a week.`;
    return `<div class="banner ${rec.level === "good" ? "good" : rec.level === "bad" ? "bad" : "warn"}"><b>Outlook: ${esc(rec.label)}.</b> The outlook adds up three standard signals (trend, momentum, crowding). In a ${bt.years || 2}-year test on ${bt.coins || 47} coins it called the direction right ${Math.round((bt.hit || 0.5) * 1000) / 10}% of the time: a coin flip.${live} Use it as context, not as a reason to trade.</div>`;
  }
  function changes(S) {
    if (stale) { $("changes").innerHTML = `<p class="muted">Hidden: the latest check is out of date.</p>`; return; }
    const ch = S.changes || []; const W = { opened: "opened", closed: "closed", flipped: "flipped to", added: "added to", cut: "cut" };
    $("changes").innerHTML = !ch.length ? `<p class="muted">No position opened, closed, flipped or resized by a quarter or more since the last check (15 minutes).</p>`
      : `<div class="table-wrap"><table><thead><tr><th>Whale</th><th>What</th><th>Coin</th><th class="num">Money moved</th><th class="num">Size now</th><th class="num">Entry price</th><th class="num">Price</th></tr></thead><tbody>
        ${ch.map((x) => `<tr data-alert-key="chg-${esc(x.addr)}-${esc(x.coin)}-${esc(x.kind)}"><td class="small">${link(x.addr, x.name)}</td><td>${esc(W[x.kind] || x.kind)} ${x.kind === "closed" ? "" : x.side === "long" ? '<span class="up">long</span>' : '<span class="down">short</span>'}</td><td><b>${esc(x.coin)}</b></td><td class="num">${usd(x.moved ?? x.notional)}</td><td class="num">${x.kind === "closed" ? "—" : usd(x.notional)}</td><td class="num">${px(x.entry)}</td><td class="num">${px(x.price)}</td></tr>`).join("")}
        </tbody></table></div><p class="small muted">Copying single moves like these didn't pay in our research (see below): what worked was holding what they hold, all of it. You can get a pop-up for each move in Alerts (top right): “Whale moves”, with the size range you choose.</p>`;
  }
  function leaders(S) {
    const l = S.leaders || [];
    $("leaders").innerHTML = `<div class="table-wrap"><table><thead><tr><th class="num">#</th><th>Whale</th><th class="num">Account</th><th class="num">Last month when picked</th><th class="num">Month so far</th><th class="num">Positions</th><th class="num">Leverage</th></tr></thead><tbody>
      ${l.map((w) => `<tr><td class="num">${w.rank}</td><td class="small">${link(w.addr, w.name)}</td><td class="num">${w.acct_now === 0 ? '<span class="muted small">moved out</span>' : usd(w.acct_now ?? w.acct)}</td><td class="num ${cls(w.roi_m)}">${pct(w.roi_m, 0)}</td><td class="num ${cls(w.roi_m_now)}">${pct(w.roi_m_now, 0)}</td><td class="num">${w.n_pos}</td><td class="num">${fin(w.gross_lev) ? w.gross_lev.toFixed(1) + "x" : "–"}</td></tr>`).join("")}
      </tbody></table></div><p class="small muted">Picked once a month: the best last-month profit relative to their account, among accounts of $1M+ that traded this week, trade less than 30x their account a month (so not market-making bots) and are profitable overall. "Month so far" is Hyperliquid's own rolling 30-day figure. "Moved out": no money in their futures account right now, so the mirror holds that share in cash.</p>`;
  }
  function research(S) {
    const r = S.research || {};
    $("research").innerHTML = `<p>We checked whether this could work before building it: ${r.whales} randomly chosen active whales and ${(r.trades || 0).toLocaleString()} of their trades over 60 days.</p>
      <ul class="why"><li><b>Winners tended to keep winning.</b> The best 20% of one month made a median ${pct(r.top_next)} the next month, and ${pc0(r.top_profitable)} stayed profitable; the worst 20% made ${pct(r.bottom_next)} (${pc0(r.bottom_profitable)} profitable). That's a real link between one month and the next, not luck.</li>
      <li><b>Copying single trades did not work.</b> Copying their big opening trades and holding for 1, 4 or 24 hours made about nothing before costs, even with no delay. Their profit comes from how they manage the whole position, which is why this page copies all of it.</li>
      <li><b>Not a get-rich-quick system.</b> A good month for the best whales was around +7%, and a quarter of them lost money. The paper mirror shows what copying them really does after costs; the verdict comes after 30 days and is re-checked every month.</li></ul>`;
  }
  async function load() {
    try {
      const r = await fetch(`${DATA}snapshot.json?t=${Date.now()}`, { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      const S = await r.json();
      const age = (Date.now() - Date.parse(S.generated_at)) / 60000; stale = !(age <= STALE_MIN);
      $("status").textContent = `Checked ${Math.round(age)} min ago`; $("status").className = `status${stale ? " stale" : ""}`;
      summary(S); mirror(S); consensus(S); changes(S); leaders(S); research(S);
    } catch {
      $("summary").className = "banner warn";
      $("summary").textContent = "Couldn't load the whale tracker data. It's produced by the project's GitHub Actions; try again in a few minutes.";
    }
  }
  load();
  setInterval(load, 5 * 60 * 1000);
})();
