/* Prediction markets (paper only): Kalshi and Polymarket markets closing this week, the one tested rule (favourites
   priced 55-70c, bought 1-7 days before the scheduled end), its paper trades at the real ask plus fees, and the
   honest record. Reads predict/run.py's snapshot (hourly). An old snapshot hides the live parts. */
(() => {
  "use strict";
  const DATA = window.OMEGA_PREDICT_DATA || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/data/predict/";
  const STALE_MIN = 150;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fin = (x) => typeof x === "number" && Number.isFinite(x);
  const pct = (x, d = 1) => (fin(x) ? `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(d)}%` : "–");
  const pc0 = (x) => (fin(x) ? `${Math.round(x * 100)}%` : "–");
  const cents = (x) => { if (!fin(x)) return "–"; const c = Math.round(x * 1000) / 10; return `${Number.isInteger(c) ? c : c.toFixed(1)}¢`; };
  const cls = (x) => (fin(x) ? (x > 0 ? "up" : x < 0 ? "down" : "") : "");
  const vol = (x, v) => (!fin(x) ? "–" : v === "kalshi" ? `${x >= 1e6 ? (x / 1e6).toFixed(1) + "M" : Math.round(x / 1e3) + "k"} contracts` : x >= 1e6 ? `$${(x / 1e6).toFixed(1)}M` : `$${Math.round(x / 1e3)}k`);
  const left = (t) => { const h = (t - Date.now()) / 3600e3; return h < 0 ? "ended" : h < 48 ? `${Math.round(h)} h` : `${(h / 24).toFixed(1)} days`; };
  const when = (t) => new Date(t).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const VENUE = { kalshi: "Kalshi", polymarket: "Polymarket" };
  const side = (s) => `<span class="pill ${s === "yes" ? "good" : "bad"}">${s.toUpperCase()}</span>`;
  const q = (x) => `<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.q)}</a> <span class="small muted">${VENUE[x.venue] || x.venue}</span>`;
  const LV = { good: "good", bad: "bad", warn: "calm" };
  let stale = true;

  function summary(S) {
    const r = S.record || {}; const c = S.counts || {};
    $("summary").className = `banner ${LV[r.level] || "calm"}`;
    $("summary").innerHTML = `<b>Paper only.</b> Every hour we read every open market on Kalshi and Polymarket (${(c.scanned || 0).toLocaleString()} right now, ${c.liquid ?? "–"} with real trading) and paper-buy the ones that fit the <b>one rule that looked promising in our research</b>: the favourite, priced 55-70¢, 1-7 days before it ends, at the real asking price plus fees, one per event.
      <b>Verdict: ${esc(r.label || "Not proven yet")}.</b> ${esc(r.text || "")}`;
  }
  function picks(S) {
    if (stale) { $("picks").innerHTML = `<div class="banner warn">The latest check is out of date, so current picks are hidden.</div>`; return; }
    const p = S.picks || [];
    $("picks").innerHTML = !p.length ? `<p class="muted">No market fits the rule right now. That's normal: it only fires for favourites priced 55-70¢ in liquid markets ending in 1-7 days.</p>`
      : `<div class="table-wrap"><table><thead><tr><th>Market</th><th>Buy</th><th class="num">Price (ask)</th><th class="num">Fee</th><th class="num">Pays if right</th><th class="num">Ends in</th><th class="num">Traded</th></tr></thead><tbody>
        ${p.map((x) => `<tr data-alert-key="pick-${esc(x.venue)}-${esc(x.id)}"><td style="white-space:normal;min-width:260px">${q(x)}</td><td>${side(x.side)}</td><td class="num">${cents(x.ask)}</td><td class="num">${cents(x.fee)}</td><td class="num up">${pct(1 / (x.ask + x.fee) - 1, 0)}</td><td class="num">${left(x.end)}</td><td class="num small">${vol(x.vol, x.venue)}</td></tr>`).join("")}
        </tbody></table></div>
        <p class="small muted">"Buy NO" means buying the other outcome: for "Las Vegas wins", NO pays if Las Vegas does not win. A contract pays $1 if right and $0 if wrong, so at 62¢ plus a 1.6¢ fee, being right pays about +57% and being wrong loses 100%. <b>Not proven:</b> the rule needs ${S.record?.min_trades || 50} settled paper trades before it can be trusted.</p>`;
  }
  function openTrades(S) {
    const o = S.open || [];
    $("open").innerHTML = !o.length ? `<p class="muted">No paper trades open yet.</p>`
      : `<div class="table-wrap"><table><thead><tr><th>Market</th><th>Side</th><th class="num">Bought at</th><th class="num">Price now</th><th class="num">Ends</th></tr></thead><tbody>
        ${o.map((x) => `<tr><td style="white-space:normal;min-width:260px">${q(x)}</td><td>${side(x.side)}</td><td class="num">${cents(x.ask)}</td><td class="num ${cls((x.now ?? NaN) - x.ask)}">${stale ? "–" : cents(x.now)}</td><td class="num">${when(x.end)}</td></tr>`).join("")}
        </tbody></table></div><p class="small muted">Each is settled automatically by the market's own result. "Price now" is the middle of the buy and sell price at the last check.</p>`;
  }
  function results(S) {
    const r = S.record || {}; const rc = S.recent || [];
    const stats = `<div class="stats"><div class="stat"><div class="k">Settled paper trades</div><div class="v">${r.settled ?? 0}</div><div class="small muted">verdict after ${r.min_trades || 50}</div></div>
      <div class="stat"><div class="k">Won</div><div class="v">${pc0(r.won)}</div><div class="small muted">the prices said ${pc0(r.price)}</div></div>
      <div class="stat"><div class="k">Average per $1</div><div class="v ${cls(r.avg)}">${pct(r.avg)}</div><div class="small muted">after the ask and fees</div></div>
      <div class="stat"><div class="k">Open now</div><div class="v">${r.open ?? 0}</div></div></div>`;
    const venues = Object.entries(r.by_venue || {}).map(([v, x]) => `${VENUE[v]}: ${x.n} trade(s), won ${pc0(x.won)}, ${pct(x.avg)} per $1`).join(" · ");
    $("results").innerHTML = stats + (venues ? `<p class="small muted">${esc(venues)}</p>` : "") + (!rc.length ? `<p class="muted">The first results arrive as the first markets resolve, within about a week.</p>`
      : `<div class="table-wrap"><table><thead><tr><th>Market</th><th>Side</th><th class="num">Bought at</th><th>Result</th><th class="num">Per $1</th></tr></thead><tbody>
        ${rc.map((x) => `<tr><td style="white-space:normal;min-width:260px">${q(x)}</td><td>${side(x.side)}</td><td class="num">${cents(x.ask)}</td><td>${x.won ? '<span class="pill good">right</span>' : '<span class="pill bad">wrong</span>'}</td><td class="num ${cls(x.net)}">${pct(x.net, 0)}</td></tr>`).join("")}
        </tbody></table></div>`);
  }
  function markets(S) {
    if (stale) { $("markets").innerHTML = `<p class="muted">Hidden: the latest check is out of date.</p>`; return; }
    const m = S.markets || [];
    $("markets").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Market</th><th>Favourite</th><th class="num">Price</th><th class="num">Spread</th><th class="num">Ends in</th><th class="num">Traded (24 h)</th><th>Fits the rule</th></tr></thead><tbody>
      ${m.map((x) => `<tr><td style="white-space:normal;min-width:260px">${q(x)}</td><td>${side(x.side)}</td><td class="num">${cents(x.price)}</td><td class="num">${cents(x.spread)}</td><td class="num">${left(x.end)}</td><td class="num small">${vol(x.vol24, x.venue)}</td><td>${x.fits ? '<span class="pill calm">yes</span>' : ""}</td></tr>`).join("")}
      </tbody></table></div><p class="small muted">The most-traded markets ending in the next 8 days, with a spread of 4¢ or less. Information, not picks.</p>`;
  }
  function research(S) {
    const r = S.research || {}; const rows = r.rows || [];
    const cell = (x) => (x ? `<td class="num">${x.n}</td><td class="num">${pc0(x.won)} <span class="muted small">vs ${pc0(x.price)}</span></td><td class="num ${cls(x.ret)}">${pct(x.ret, 0)}</td>` : "<td></td><td></td><td></td>");
    $("research").innerHTML = `<p>Before building this we tested ${(r.markets || 0).toLocaleString()} resolved yes/no Polymarket markets with $${(r.min_volume || 0).toLocaleString()}+ traded (${esc(r.period || "")}). The idea: crowds overpay for long shots and underpay for likely outcomes, so buying the favourite might pay.</p>
      <div class="table-wrap"><table><thead><tr><th>Buy the favourite at</th><th class="num">1 day before: trades</th><th class="num">won</th><th class="num">per $1</th><th class="num">7 days before: trades</th><th class="num">won</th><th class="num">per $1</th></tr></thead><tbody>
      ${rows.map((x) => `<tr${x.band === "55-70c" ? ' style="font-weight:600"' : ""}><td>${esc(x.band)}</td>${cell(x.d1)}${cell(x.d7)}</tr>`).join("")}</tbody></table></div>
      <ul class="why"><li><b>Fair test:</b> decided at a time known in advance (1 or 7 days before the scheduled end), only while the market was still open, on a price actually traded in the 6 hours before, minus a 1¢ cost.</li>
      <li><b>A trap we avoided:</b> measured "1 day before close", the same data looked like +13% to +19% a trade. That was hindsight: many markets close the moment the event happens, and some prices were stale after the result was known.</li>
      <li><b>Only 55-70¢ looked good</b>, in both halves of the period, but on about 57 trades, and it was one of 15 combinations we looked at, so it could still be luck. Heavy favourites (80-95¢) <b>lost</b> money, and all favourites together had no reliable edge.</li>
      <li>That's why this page tests the rule forward, on paper, at the real asking price and fees, before calling it anything.</li></ul>`;
  }
  async function load() {
    try {
      const r = await fetch(`${DATA}snapshot.json?t=${Date.now()}`, { cache: "no-store" }); if (!r.ok) throw new Error(r.status);
      const S = await r.json();
      stale = (Date.now() - Date.parse(S.generated_at)) / 60000 > STALE_MIN;
      summary(S); picks(S); openTrades(S); results(S); markets(S); research(S);
      const st = document.querySelector(".top .status") || $("status");
      if (st) st.textContent = `Checked ${Math.round((Date.now() - Date.parse(S.generated_at)) / 60000)} min ago`;
    } catch (e) {
      $("summary").className = "banner warn"; $("summary").textContent = "Couldn't load the latest prediction-market data. It updates hourly: try again soon.";
    }
  }
  load(); setInterval(load, 10 * 60e3);
})();
