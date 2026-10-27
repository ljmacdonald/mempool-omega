/* $100 funding account (DECISIONS D108): from carry/run.py (data branch carry/snapshot.json, refreshed hourly).
   Shows what the file says; nothing is recomputed here. */
(() => {
  "use strict";
  const DATA = window.OMEGA_CARRY_DATA || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/data/carry/";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fin = (x) => typeof x === "number" && Number.isFinite(x);
  const usd = (x, dp = 2) => (fin(x) ? `${x < 0 ? "−" : ""}$${Math.abs(x).toFixed(dp)}` : "–");
  const pct = (x, dp = 4) => (fin(x) ? `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(dp)}%` : "–");
  const when = (t) => (t ? new Date(t).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "–");

  function render(S) {
    const a = S.account || {};
    const held = Object.keys(a.positions || {});
    const ch = (a.equity ?? 100) - (a.start ?? 100);
    $("summary").className = `banner ${ch > 0 ? "good" : "calm"}`;
    $("summary").innerHTML = `<b>${usd(a.equity)} from ${usd(a.start)}.</b> ${held.length ? `Holding ${held.length} pair${held.length === 1 ? "" : "s"} (${esc(held.join(", "))}); the rest waits in cash until a coin's funding qualifies.` : "In cash: no coin's funding qualifies right now."} Funding collected so far: ${usd(a.funding_total, 4)}.`;
    $("account").innerHTML = `<div class="stats">
        <div class="stat"><div class="k">Account value</div><div class="v ${ch > 0 ? "up" : ch < 0 ? "down" : ""}">${usd(a.equity)}</div><div class="small muted">since ${esc(when(a.started_at))}</div></div>
        <div class="stat"><div class="k">Funding collected</div><div class="v ${a.funding_total >= 0 ? "up" : "down"}">${usd(a.funding_total, 4)}</div><div class="small muted">received minus paid</div></div>
        <div class="stat"><div class="k">Fees and gaps paid</div><div class="v">${usd(a.fees_total, 4)}</div><div class="small muted">${a.trades || 0} open${a.trades === 1 ? "" : "s"} and close${a.trades === 1 ? "" : "s"} in all</div></div>
        <div class="stat"><div class="k">Cash waiting</div><div class="v">${usd(a.cash)}</div><div class="small muted">not in a pair</div></div></div>
      <p class="small muted">The value counts each pair at what it would fetch if closed now (coin at the bid, future at the ask), before closing fees. Paper only.</p>`;

    const coins = S.coins || [];
    $("coins").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Coin</th><th>In the account</th><th class="num">Funding this period</th><th class="num">Last 7 days</th><th class="num">At that pace, a year</th><th class="num">Future vs coin</th><th>Next payment</th></tr></thead><tbody>
      ${coins.map((c) => `<tr><td><b>${esc(c.coin)}</b></td><td>${c.held ? '<span class="pill good">Held</span>' : '<span class="pill calm">Waiting</span>'}</td>
        <td class="num ${c.rate_now >= 0 ? "up" : "down"}">${pct(c.rate_now)}</td><td class="num ${c.sum_7d >= 0 ? "up" : "down"}">${pct(c.sum_7d, 3)}</td>
        <td class="num">${pct(c.per_year, 1)}</td><td class="num">${pct(c.basis, 3)}</td><td>${esc(when(c.next_funding))}</td></tr>`).join("")}
      </tbody></table></div>
      <p class="small muted">"A year" is on the pair's own size; the account earns about half that on its money, because the other half is the future's margin. A coin joins when its last 7 days of funding add up to more than ${pct((S.rules || {}).enter_7d, 2)}, and leaves when they turn negative.</p>`;

    const days = S.funding_by_day || [];
    $("byday").innerHTML = !days.length ? '<p class="muted">No funding payment yet: payments come every few hours once a pair is open.</p>'
      : `<div class="table-wrap"><table><thead><tr><th>Day (UTC)</th><th class="num">Funding</th></tr></thead><tbody>${days.map(([d, v]) => `<tr><td>${esc(d)}</td><td class="num ${v >= 0 ? "up" : "down"}">${usd(v, 5)}</td></tr>`).join("")}</tbody></table></div>`;

    const tr = S.trades || [];
    $("trades").innerHTML = (!tr.length ? '<p class="muted">None yet.</p>' : `<div class="table-wrap"><table><thead><tr><th>Time</th><th>Coin</th><th>Action</th><th class="num">Coin price</th><th class="num">Future price</th><th class="num">Amount</th><th class="num">Fees and gaps</th><th class="num">Funding while open</th></tr></thead><tbody>
      ${tr.map((t) => `<tr><td>${esc(when(t.time))}</td><td>${esc(t.coin)}</td><td>${t.action === "open" ? '<span class="pill good">Opened</span>' : `<span class="pill calm">Closed</span><div class="small muted">${esc(t.why)}</div>`}</td>
        <td class="num">${usd(+t.spot_price, 4)}</td><td class="num">${usd(+t.perp_price, 4)}</td><td class="num">${usd(+t.usd)}</td><td class="num">${usd(+t.fees_usd, 4)}</td><td class="num">${t.funding_usd === "" ? "–" : usd(+t.funding_usd, 4)}</td></tr>`).join("")}</tbody></table></div>`)
      + ((S.log || []).length ? `<details><summary>Every step, in words</summary><ul class="rules">${S.log.slice(0, 30).map((x) => `<li><span class="small muted">${esc(when(x.t))}</span> ${esc(x.text)}</li>`).join("")}</ul></details>` : "");

    const r = S.rules || {};
    $("rules").innerHTML = `<ul class="rules">
      <li>Basket: ${esc((r.basket || []).join(", "))}, $${(100 / ((r.basket || []).length || 1)).toFixed(0)} each: half buys the coin, half is margin for an equal short in its future (no leverage). Prices and funding from OKX.</li>
      <li>Join when the last 7 days of funding add up to more than ${pct(r.enter_7d, 2)}; leave when they turn negative; leave at once if the future's loss reaches ${Math.round((r.risk_stop || 0) * 100)}% of its margin; a closed coin waits a day before rejoining.</li>
      <li>Every open and close pays the coin fee (${pct(r.spot_fee, 2)}), the future fee (${pct(r.perp_fee, 2)}) and the buy-sell gap, at live prices.</li></ul>`;
    const st = document.querySelector(".top .status") || $("status");
    if (st) st.textContent = `Updated ${when(S.generated_at)}`;
  }

  async function load() {
    try {
      const r = await fetch(`${DATA}snapshot.json?t=${Date.now()}`, { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      render(await r.json());
    } catch {
      $("summary").className = "banner warn";
      $("summary").textContent = "The funding account's data isn't available yet. It updates every hour.";
    }
  }
  load(); setInterval(load, 10 * 60e3);
})();
