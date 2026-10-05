/* Strategy lab: famous public strategies re-tested by lab/run.py (nightly) with where each one stands now (every
   15 minutes). Reads the snapshot published on the data branch. Prices shown are always labelled with the time
   they're from; when the snapshot is old, positions are hidden instead of shown as if current. */
(() => {
  "use strict";
  const DATA = window.OMEGA_LAB_DATA || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/data/lab/";
  const STALE_MIN = 45;
  const LEVEL = { good: [0, "Held up", "good"], warn: [1, "Not proven", "warn"], bad: [2, "Failed", "bad"] };
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fin = (x) => typeof x === "number" && Number.isFinite(x);
  const pct = (x, d = 2) => (fin(x) ? `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(d)}%` : "–");
  const pc0 = (x) => (fin(x) ? `${Math.round(x * 100)}%` : "–");
  const num = (x) => (fin(x) ? (Math.abs(x) >= 1000 ? x.toLocaleString(undefined, { maximumFractionDigits: 2 }) : Math.abs(x) >= 1 ? x.toFixed(2) : x.toPrecision(4)) : "–");
  const day = (t) => new Date(t).toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" });
  const when = (t) => new Date(t).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const hold = (h) => (!fin(h) ? "–" : h < 2 ? `${Math.round(h * 60)} min` : h < 48 ? `${h.toFixed(1)} h` : `${(h / 24).toFixed(1)} days`);
  const cls = (x) => (fin(x) ? (x > 0 ? "up" : "down") : "");

  function row(name, a) {
    return `<tr><td>${name}</td><td class="num">${a.n ?? 0}</td><td class="num">${pc0(a.hit)}</td><td class="num ${cls(a.avg)}">${pct(a.avg)}</td><td class="num">${pct(a.random)}</td><td class="num ${cls(a.edge)}">${pct(a.edge)}</td></tr>`;
  }
  const HEAD = `<thead><tr><th></th><th class="num">Trades</th><th class="num">Hit rate</th><th class="num">Average per trade after costs</th><th class="num">Random entries</th><th class="num">Better than random by</th></tr></thead>`;

  function board(S) {
    const list = Object.entries(S.strategies).filter(([, v]) => v.stats).sort((a, b) => LEVEL[a[1].stats.level][0] - LEVEL[b[1].stats.level][0]);
    $("board").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Strategy</th><th>Style</th><th class="num">Trades</th><th class="num">Hit rate</th><th class="num">Average per trade</th><th class="num">Random entries</th><th>Verdict</th></tr></thead><tbody>
      ${list.map(([k, v]) => { const a = v.stats.all; const L = LEVEL[v.stats.level];
        return `<tr><td><a href="#s-${k}"><b>${esc(v.name)}</b></a></td><td class="small">${esc(v.style)}</td><td class="num">${a.n}</td><td class="num">${pc0(a.hit)}</td><td class="num ${cls(a.avg)}">${pct(a.avg)}</td><td class="num">${pct(a.random)}</td><td><span class="pill ${L[2]}">${L[1]}</span></td></tr>`; }).join("")}
      </tbody></table></div>`;
    const good = list.filter(([, v]) => v.stats.level === "good").map(([, v]) => v.name);
    const hiHit = list.filter(([, v]) => v.stats.all.hit >= 0.8 && !(v.stats.all.avg > 0)).map(([, v]) => `${v.name} (${pc0(v.stats.all.hit)} hit rate)`);
    $("summary").className = `banner ${good.length ? "good" : "warn"}`;
    $("summary").innerHTML = `<b>${list.length} famous public strategies re-tested. ${good.length ? `${good.length} held up: ${esc(good.join(", "))}.` : "None held up."}</b> ` +
      `${list.length - good.length} did not: they lost money after costs, did no better than random entries, or stopped working once they were published.` +
      (hiHit.length ? ` Note the trap: ${esc(hiHit.join(", "))} won most of its trades and still lost money overall.` : "") +
      ` <span class="small">Tested ${S.stats_at ? `on ${day(Date.parse(S.stats_at))}` : "nightly"}.</span>`;
  }

  function nowBlock(k, v, stale) {
    const open = v.open || [];
    let html = "";
    if (v.watch && Object.keys(v.watch).length) {
      html += `<div class="table-wrap"><table><thead><tr><th>Market</th><th class="num">2-day RSI</th><th>Above 200-day average?</th><th>Buy signal at the close?</th></tr></thead><tbody>
        ${Object.entries(v.watch).map(([sym, w]) => { const m = v.markets.find((x) => x.sym === sym) || { label: sym }; const sig = w.above200 && w.rsi2 < 10;
          return `<tr><td>${esc(m.label)}</td><td class="num">${fin(w.rsi2) ? w.rsi2.toFixed(0) : "–"}</td><td>${w.above200 ? "Yes" : "No: no buying"}</td><td>${sig ? '<span class="pill good">Yes</span>' : `No${w.above200 ? " (needs RSI under 10)" : ""}`}</td></tr>`; }).join("")}
        </tbody></table></div><p class="small muted">From the last finished trading day (${v.asof && Object.values(v.asof)[0] ? `closed ${when(Object.values(v.asof)[0])}` : ""}). The rule buys at the close, so in practice the order goes in during the last minutes of the session.</p>`;
    }
    if (!open.length) return html + `<p class="muted">Not in any trade right now.</p>`;
    if (stale) return html + `<div class="banner warn">The latest update is more than ${STALE_MIN} minutes old, so open positions are hidden rather than shown with old prices.</div>`;
    return html + `<div class="table-wrap"><table><thead><tr><th>Market</th><th>Side</th><th>Entered</th><th class="num">Entry</th><th class="num">Price then</th><th class="num">So far</th><th class="num">Safety exit</th>${open.some((o) => o.target) ? '<th class="num">Take profit</th>' : ""}</tr></thead><tbody>
      ${open.map((o) => { const so = o.side * (o.last / o.entry - 1);
        return `<tr><td>${esc(o.label)}</td><td>${o.side > 0 ? "Buy" : "Sell"}</td><td class="small">${when(o.t_in)}</td><td class="num">${num(o.entry)}</td><td class="num">${num(o.last)} <span class="muted small">at ${when(o.asof)}</span></td><td class="num ${cls(so)}">${pct(so)}</td><td class="num">${o.stop ? num(o.stop) : "none"}</td>${open.some((x) => x.target) ? `<td class="num">${o.target ? num(o.target) : "–"}</td>` : ""}</tr>`; }).join("")}
      </tbody></table></div><p class="small muted">The strategy's own paper positions, not a recommendation${v.stats && v.stats.level !== "good" ? ": this strategy did <b>not</b> hold up in the tests" : ""}. "Price then" is the last finished candle's close, not a live price.</p>`;
  }

  function card(k, v, stale) {
    const s = v.stats; const L = LEVEL[s.level]; const a = s.all;
    const later = s.split === "publication" ? `After it was published (${day(Date.parse(v.published))} on)` : "Most recent third";
    const earlier = s.split === "publication" ? "Before it was published" : "First two thirds";
    const markets = Object.values(s.markets || {});
    return `<article class="card" id="s-${k}">
      <h2>${esc(v.name)} <span class="pill ${L[2]}">${L[1]}</span></h2>
      <p class="small muted">${esc(v.style)} · ${esc(v.tf === "1d" ? "daily candles" : v.tf === "1h" ? "1-hour candles" : "5-minute candles")} · source: <a href="${esc(v.url)}" target="_blank" rel="noopener">${esc(v.source)}</a></p>
      <p><b>The rules:</b> ${esc(v.rules)}</p>
      <p><b>What's claimed:</b> ${esc(v.claim)}</p>
      <div class="banner ${L[2]}"><b>Our test:</b> ${esc(s.verdict)}</div>
      <div class="stats">
        <div class="stat"><div class="k">Trades tested</div><div class="v">${a.n}</div></div>
        <div class="stat"><div class="k">Hit rate</div><div class="v">${pc0(a.hit)}</div></div>
        <div class="stat"><div class="k">Average per trade</div><div class="v ${cls(a.avg)}">${pct(a.avg)}</div></div>
        <div class="stat"><div class="k">Random entries</div><div class="v">${pct(a.random)}</div></div>
        <div class="stat"><div class="k">Average win / loss</div><div class="v">${pct(a.avg_win, 1)} / ${pct(a.avg_loss, 1)}</div></div>
        <div class="stat"><div class="k">Average time in a trade</div><div class="v">${hold(a.hold_h)}</div></div>
        ${fin(a.avg_r) ? `<div class="stat"><div class="k">Per $1 risked</div><div class="v ${cls(a.avg_r)}">${a.avg_r >= 0 ? "+" : "−"}${Math.abs(a.avg_r).toFixed(2)}</div></div>` : ""}
      </div>
      <p class="small muted">Tested ${s.from ? day(s.from) : "?"} to ${s.to ? day(s.to) : "?"}. Costs per round trip: ${esc(v.markets.map((m) => `${m.label} ${(m.cost * 100).toFixed(2)}%`).join(", "))}.</p>
      <div class="table-wrap"><table>${HEAD}<tbody>${row(earlier, s.early)}${row(`<b>${later}</b>`, s.late)}${s.since_added && s.since_added.n ? row("Since it was added to this site", s.since_added) : ""}</tbody></table></div>
      <details><summary>Each market</summary><div class="table-wrap"><table>${HEAD}<tbody>${markets.map((m) => row(esc(m.label), m)).join("")}</tbody></table></div></details>
      <h3>Right now</h3>${nowBlock(k, v, stale)}
      ${(s.recent || []).length ? `<details><summary>Latest finished trades</summary><div class="table-wrap"><table><thead><tr><th>Closed</th><th>Market</th><th>Side</th><th class="num">Result after costs</th><th>Why it closed</th></tr></thead><tbody>
        ${s.recent.map((r) => `<tr><td class="small">${when(r.t_out)}</td><td>${esc(r.label)}</td><td>${r.side > 0 ? "Buy" : "Sell"}</td><td class="num ${cls(r.net)}">${pct(r.net)}</td><td class="small">${esc(r.reason)}</td></tr>`).join("")}
        </tbody></table></div></details>` : ""}
    </article>`;
  }

  async function load() {
    try {
      const r = await fetch(`${DATA}snapshot.json?t=${Date.now()}`, { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      const S = await r.json();
      const age = (Date.now() - Date.parse(S.generated_at)) / 60000; const stale = !(age <= STALE_MIN);
      $("status").textContent = `Updated ${Math.round(age)} min ago`;
      $("status").className = `status${stale ? " stale" : ""}`;
      board(S);
      const list = Object.entries(S.strategies).filter(([, v]) => v.stats).sort((a, b) => LEVEL[a[1].stats.level][0] - LEVEL[b[1].stats.level][0]);
      $("cards").innerHTML = list.map(([k, v]) => card(k, v, stale)).join("");
    } catch (e) {
      $("summary").className = "banner warn";
      $("summary").textContent = "Couldn't load the test results. They are produced by the project's GitHub Actions; try again in a few minutes.";
    }
  }
  load();
  setInterval(load, 5 * 60 * 1000);
})();
