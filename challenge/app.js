/* The $100 DEX challenge (paper only, DECISIONS D95): reads dex/challenge.py's files (account, every step, finished
   trades, account value each hour) and shows them. Nothing is computed here that changes the account. */
(() => {
  "use strict";
  const DATA = window.OMEGA_REPO || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/main/state/";
  const D = DATA + ((window.OMEGA_CHALLENGE || {}).dir || "dex/challenge/");     // the Small-coin page sets its own (D97)
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fin = (x) => typeof x === "number" && Number.isFinite(x);
  const usd = (x) => (fin(x) ? `${x < 0 ? "−" : ""}$${Math.abs(x).toFixed(2)}` : "–");
  const pct = (x, d = 1) => (fin(x) ? `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(d)}%` : "–");
  const cls = (x) => (fin(x) ? (x > 0 ? "up" : x < 0 ? "down" : "") : "");
  const px = (x) => (fin(x) ? (x >= 1 ? x.toFixed(4) : x.toPrecision(4)) : "–");
  const when = (t) => new Date(t).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const CH = { solana: "Solana", bsc: "BNB Chain", ethereum: "Ethereum", robinhood: "Robinhood Chain", base: "Base", binance: "Binance" };
  const COST = { fee: "fee", impact: "price impact", mev: "sandwich bots", tax: "tax", gas: "network fee", spread_slippage: "buy-sell gap and slippage" };
  const KIND = { buy: ["Bought", "good"], sell: ["Sold", "calm"], order: ["Decided to buy", "calm"], check: ["Hourly check", ""], skip: ["Cancelled", "warn"], pause: ["Paused", "bad"] };
  const get = async (f, json = true) => { const r = await fetch(`${D}${f}?t=${Date.now()}`, { cache: "no-store" }); if (!r.ok) throw new Error(r.status); return json ? r.json() : r.text(); };
  const csv = (text) => { const l = text.trim().split(/\r?\n/); const h = l.shift().split(","); return l.map((x) => { const v = x.split(","); return Object.fromEntries(h.map((k, i) => [k, v[i] !== undefined && v[i] !== "" && !Number.isNaN(+v[i]) ? +v[i] : v[i]])); }); };

  function summary(a) {
    const eq = a.equity, start = a.start || 100, open = (a.positions || []).filter((p) => p.status === "open"), pend = (a.positions || []).filter((p) => p.status === "pending");
    const dayChg = fin(a.day_start) && a.day_start > 0 ? eq / a.day_start - 1 : NaN;
    const status = a.paused ? `<span class="pill bad">Paused</span> ${esc(a.pause_reason)}`
      : dayChg <= -0.05 ? `<span class="pill warn">Today's loss limit hit</span> no new trades until tomorrow (UTC)`
        : open.length + pend.length ? `<span class="pill good">Trading</span> ${open.length} open${pend.length ? `, ${pend.length} being bought` : ""}` : `<span class="pill calm">In cash</span> waiting for a trade worth the risk`;
    $("summary").className = `banner ${eq >= start ? "good" : "warn"}`;
    $("summary").innerHTML = `<div class="stats">
      <div class="stat"><div class="k">Account value</div><div class="v ${cls(eq - start)}">${usd(eq)}</div><div class="small muted">${pct(eq / start - 1)} since the start</div></div>
      <div class="stat"><div class="k">Doing nothing</div><div class="v">${usd(start)}</div><div class="small muted">the $100 kept in cash</div></div>
      <div class="stat"><div class="k">Today</div><div class="v ${cls(dayChg)}">${pct(dayChg)}</div><div class="small muted">since 00:00 UTC</div></div>
      <div class="stat"><div class="k">Peak</div><div class="v">${usd(a.peak)}</div><div class="small muted">${pct(eq / a.peak - 1)} from it</div></div>
      <div class="stat"><div class="k">Finished trades</div><div class="v">${a.trades || 0}</div><div class="small muted">cash ${usd(a.cash)}</div></div></div>
      <p style="margin:10px 0 0">${status}. <b>Paper only.</b> Started ${a.started_at ? esc(when(a.started_at)) : "–"}; checked every hour with ${esc((window.OMEGA_CHALLENGE || {}).listName || "the DEX list")}.</p>`;
    const st = document.querySelector(".top .status") || $("status");
    if (st && a.updated) st.textContent = `Checked ${Math.max(0, Math.round((Date.now() - Date.parse(a.updated)) / 60000))} min ago`;
  }

  function chart(rows, start) {
    if (rows.length < 2) { $("chart").innerHTML = `<p class="muted">The chart starts once the account has been checked twice (hourly).</p>`; return; }
    const W = Math.max($("chart").clientWidth || 640, 280), H = 220, L = 52, R = 10, T = 12, B = 26;
    const t = rows.map((r) => Date.parse(r.t)), v = rows.map((r) => r.equity);
    const lo = Math.min(start, ...v), hi = Math.max(start, ...v), pad = Math.max((hi - lo) * 0.15, 0.5);
    const y0 = lo - pad, y1 = hi + pad, x = (i) => L + (t[i] - t[0]) / Math.max(t[t.length - 1] - t[0], 1) * (W - L - R), y = (val) => T + (1 - (val - y0) / (y1 - y0)) * (H - T - B);
    const path = v.map((val, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(val).toFixed(1)}`).join("");
    const ticks = [y0 + pad, start, y1 - pad].filter((val, i, a) => a.findIndex((b) => Math.abs(b - val) < (y1 - y0) * 0.08) === i);
    $("chart").innerHTML = `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Account value each hour, from ${usd(v[0])} to ${usd(v[v.length - 1])}">
      ${ticks.map((val) => `<line x1="${L}" x2="${W - R}" y1="${y(val)}" y2="${y(val)}" stroke="var(--line)" stroke-width="1"/><text x="${L - 6}" y="${y(val) + 4}" text-anchor="end" font-size="11" fill="var(--muted)">${usd(val)}</text>`).join("")}
      <line x1="${L}" x2="${W - R}" y1="${y(start)}" y2="${y(start)}" stroke="var(--muted)" stroke-width="1" stroke-dasharray="4 4"/>
      <path d="${path}" fill="none" stroke="var(--series)" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>
      <text x="${L}" y="${H - 6}" font-size="11" fill="var(--muted)">${esc(when(t[0]))}</text><text x="${W - R}" y="${H - 6}" text-anchor="end" font-size="11" fill="var(--muted)">${esc(when(t[t.length - 1]))}</text>
      <line class="xh" x1="0" x2="0" y1="${T}" y2="${H - B}" stroke="var(--line-strong)" stroke-width="1" visibility="hidden"/><circle class="dot" r="4" fill="var(--series)" stroke="var(--surface)" stroke-width="2" visibility="hidden"/></svg>
      <div class="tip" hidden></div></div><p class="small muted">Value each hour if everything were sold at that moment, after every cost. The dashed line is the $100 start, which is also what doing nothing would be worth.</p>`;
    const box = $("chart").querySelector(".chart"), svg = box.querySelector("svg"), tip = box.querySelector(".tip"), xh = svg.querySelector(".xh"), dot = svg.querySelector(".dot");
    const move = (ev) => {
      const r = svg.getBoundingClientRect(), mx = (ev.clientX - r.left) / r.width * W;
      let i = 0; for (let k = 1; k < v.length; k++) if (Math.abs(x(k) - mx) < Math.abs(x(i) - mx)) i = k;
      xh.setAttribute("x1", x(i)); xh.setAttribute("x2", x(i)); dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(v[i]));
      xh.setAttribute("visibility", "visible"); dot.setAttribute("visibility", "visible");
      tip.hidden = false; tip.innerHTML = `<b>${usd(v[i])}</b> <span class="muted">${esc(when(t[i]))}</span>`;
      const left = x(i) / W * r.width; tip.style.left = `${Math.min(Math.max(left - tip.offsetWidth / 2, 0), r.width - tip.offsetWidth)}px`; tip.style.top = `${y(v[i]) / H * r.height - 40}px`;
    };
    svg.addEventListener("pointermove", move); svg.addEventListener("pointerleave", () => { tip.hidden = true; xh.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); });
  }

  function openTrades(a) {
    const ps = a.positions || [];
    $("open").innerHTML = !ps.length ? `<p class="muted">None: the account is in cash.</p>`
      : `<div class="table-wrap"><table><thead><tr><th>Coin</th><th>Status</th><th class="num">Put in</th><th class="num">Bought at</th><th class="num">Price now</th><th class="num">Take profit / safety exit</th><th class="num">Worth if sold now</th></tr></thead><tbody>
        ${ps.map((p) => `<tr><td><b>${esc(p.coin)}</b> <span class="small muted">${esc(CH[p.chain] || p.chain)} · ${esc(p.grade)} ${esc(p.score)}</span></td>
          <td>${p.status === "pending" ? '<span class="pill calm">being bought</span>' : `<span class="pill good">open</span> <span class="small muted">since ${esc(when(p.fill_t))}</span>`}</td>
          <td class="num">${usd(p.amount)}</td><td class="num">${p.status === "open" ? px(p.entry) : "end of this hour"}</td><td class="num">${p.status === "open" ? px(p.last) : "–"}</td>
          <td class="num small">${p.status === "open" ? `<span class="up">${px(p.tp)}</span> / <span class="down">${px(p.stop)}</span>${p.sell_by ? `<br><span class="muted">sell by ${esc(when(p.sell_by))}</span>` : ""}` : `${pct(p.tp_pct)} / ${pct(p.sl_pct)}`}</td>
          <td class="num ${p.status === "open" ? cls(p.value - p.amount) : ""}">${p.status === "open" ? `${usd(p.value)} <span class="small">(${pct(p.value / p.amount - 1)})</span>` : "–"}</td></tr>`).join("")}
        </tbody></table></div><p class="small muted">${esc((window.OMEGA_CHALLENGE || {}).worthNote || "\"Worth if sold now\" is after the selling costs, against the pool's money right now.")}</p>`;
  }

  function steps(log) {
    const shown = log.slice(0, 40);
    $("steps").innerHTML = !log.length ? `<p class="muted">The first hourly check hasn't happened yet.</p>`
      : `<ul class="steps">${shown.map((s) => { const [label, tone] = KIND[s.kind] || [s.kind, ""]; return `<li><time datetime="${esc(s.t)}">${esc(when(s.t))}</time><div><span class="pill ${tone}">${esc(label)}</span><p>${esc(s.text)}</p></div></li>`; }).join("")}</ul>
        ${log.length > shown.length ? `<p class="small muted">Showing the latest ${shown.length} of ${log.length} steps.</p>` : ""}`;
  }

  function days(eq, trades, start) {
    if (!eq.length) { $("days").innerHTML = `<p class="muted">The first day starts with the first hourly check.</p>`; return; }
    const by = new Map();
    eq.forEach((r) => by.set(String(r.t).slice(0, 10), r.equity));
    const ds = [...by.entries()];
    const nTr = (d) => trades.filter((t) => String(t.closed).slice(0, 10) === d);
    $("days").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Day (UTC)</th><th class="num">Value at day end</th><th class="num">Change that day</th><th class="num">Finished trades</th><th class="num">Their result</th></tr></thead><tbody>
      ${ds.reverse().map(([d, v], i, a) => { const prev = i + 1 < a.length ? a[i + 1][1] : start; const tr = nTr(d); const net = tr.reduce((s, t) => s + (+t.net || 0), 0);
        return `<tr><td>${esc(d)}</td><td class="num">${usd(v)}</td><td class="num ${cls(v - prev)}">${usd(v - prev)} <span class="small">(${pct(v / prev - 1)})</span></td><td class="num">${tr.length}</td><td class="num ${tr.length ? cls(net) : ""}">${tr.length ? usd(net) : "–"}</td></tr>`; }).join("")}
      </tbody></table></div>`;
  }

  function finished(trades) {
    $("trades").innerHTML = !trades.length ? `<p class="muted">No finished trades yet. Staying in cash until a trade is worth the risk is part of the plan.</p>`
      : `<div class="table-wrap"><table><thead><tr><th>Coin</th><th>Bought → sold</th><th class="num">Put in</th><th class="num">Price in → out</th><th>Why it closed</th><th class="num">Selling costs</th><th class="num">Result after every cost</th></tr></thead><tbody>
        ${trades.slice().reverse().map((t) => { const ks = Object.keys(COST).filter((k) => fin(+t[`sell_${k}`])); const sc = ks.reduce((s, k) => s + (+t[`sell_${k}`] || 0), 0);
          return `<tr><td><b>${esc(t.coin)}</b> <span class="small muted">${esc(CH[t.chain] || t.chain)} · ${esc(t.grade)}</span></td><td class="small">${esc(when(t.opened))} → ${esc(when(t.closed))}</td>
          <td class="num">${usd(+t.amount)}</td><td class="num small">${px(+t.entry)} → ${px(+t.exit)}</td><td class="small">${esc(t.why)}</td>
          <td class="num small" title="${esc(ks.map((k) => `${COST[k]} ${usd(+t[`sell_${k}`])}`).join(", "))}">${usd(sc)}</td>
          <td class="num ${cls(+t.net)}"><b>${usd(+t.net)}</b> <span class="small">(${pct(+t.ret)})</span></td></tr>`; }).join("")}
        </tbody></table></div><p class="small muted">${esc((window.OMEGA_CHALLENGE || {}).buyNote || "The buying costs (fees, impact, bots, front-runners, tax, network fee) are already inside the result: they reduce the coins bought.")} Hover the selling costs for the breakdown.</p>`;
  }

  async function load() {
    try {
      const a = await get("account.json");
      const [log, eqT, trT] = await Promise.all([get("log.json").catch(() => []), get("equity.csv", false).catch(() => ""), get("trades.csv", false).catch(() => "")]);
      const eq = eqT ? csv(eqT) : [], tr = trT ? csv(trT) : [];
      summary(a); chart(eq.slice(-24 * 30), a.start || 100); openTrades(a); steps(log); days(eq, tr, a.start || 100); finished(tr);
    } catch (e) {
      $("summary").className = "banner calm";
      $("summary").innerHTML = "<b>The challenge starts with the next hourly DEX check.</b> The $100 paper account, every step and every trade will appear here then.";
      ["chart", "open", "steps", "days", "trades"].forEach((id) => { $(id).innerHTML = `<p class="muted">Nothing yet.</p>`; });
    }
  }
  let last = 0; addEventListener("resize", () => { clearTimeout(last); last = setTimeout(load, 300); });
  load(); setInterval(load, 5 * 60e3);
})();
