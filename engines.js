/* Recovery engines (DECISIONS D121 Engine A, D122 Engine B): renders deep/run.py's weekly report (data branch
   hayes/research.json or deep/research.json). Shows what the file says; nothing is recomputed or invented here. */
(() => {
  "use strict";
  const ENGINE = document.body.dataset.engine;
  const DATA = window.OMEGA_ENGINES_DATA || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/data/";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fin = (x) => typeof x === "number" && Number.isFinite(x);
  const pct = (x, dp = 0) => (fin(x) ? `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(dp)}%` : "–");
  const share = (x, dp = 0) => (fin(x) ? `${(x * 100).toFixed(dp)}%` : "–");
  const big = (x) => (!fin(x) ? "–" : Math.abs(x) >= 1e12 ? `$${(x / 1e12).toFixed(2)}T` : Math.abs(x) >= 1e9 ? `$${(x / 1e9).toFixed(1)}B` : `$${(x / 1e6).toFixed(0)}M`);
  const ago = (t) => { const m = Math.round((Date.now() - Date.parse(t)) / 60000); return !fin(m) ? "" : m < 60 ? `${m} min ago` : m < 2880 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`; };
  const REGIME = { risk_on: ["Risk-on", "good"], neutral: ["Neutral", "calm"], risk_off: ["Risk-off", "bad"] };
  const WAYS = { touch: "First touch of the band", asymmetric: "Asymmetric sleeve's entry", confirmed: "Confirmed sleeve's entry", entry: "All entry rules passed" };
  const pass = (ok) => `<span class="pill ${ok ? "good" : "bad"}">${ok ? "Pass" : "Fail"}</span>`;
  let S = null;

  function summary() {
    const halted = S.status !== "paper";
    const fails = Object.entries(S.kill || {}).filter(([k, v]) => k !== "status" && v && v.pass === false).length +
      Object.entries((S.kill || {}).backtest || {}).filter(([k, v]) => k !== "status" && v && v.pass === false).length;
    const age = (Date.now() - Date.parse(S.generated_at)) / 86400e3;
    $("summary").className = `banner ${halted ? "warn" : "good"}`;
    $("summary").innerHTML = halted
      ? `<b>Halted: this engine failed ${fails} of its own pass/fail tests on history</b>, so it runs no paper trades. Its rules were fixed before testing, and nothing is tuned to make it pass. The tests run again every week on the latest data; if they ever pass, this page says so and the paper account starts. <span class="small muted">Tested ${esc(ago(S.generated_at))}${age > 9 ? " (out of date: the weekly job may have failed)" : ""}.</span>`
      : `<b>Passed every test on history.</b> Paper trading only. <span class="small muted">Tested ${esc(ago(S.generated_at))}.</span>`;
    const st = $("status"); if (st) st.textContent = halted ? "Halted" : "Paper";
  }

  function regime() {
    const R = S.regime_today || {}, I = R.inputs || {}, D = I.dates || {};
    const [name, tone] = REGIME[R.state] || [R.state, "calm"];
    const row = (label, ok, now, then, dates) => `<tr><td>${label}</td><td>${ok === true ? '<span class="pill good">Yes</span>' : ok === false ? '<span class="pill bad">No</span>' : '<span class="pill calm">Unknown</span>'}</td><td class="num">${now}</td><td class="num">${then}</td><td class="small">${esc(dates)}</td></tr>`;
    $("regimeBody").innerHTML = `<p><span class="pill ${tone}">${esc(name)}</span> ${R.confirmations ?? 0} of 3 signals agree${(R.stale || []).length ? `; <b>out-of-date input${R.stale.length > 1 ? "s" : ""}: ${esc(R.stale.join(", "))}</b> (out-of-date data always counts as risk-off)` : ""}. ${ENGINE === "deep" ? "Engine B may only enter in risk-on." : "Engine A's confirmed sleeve enters in risk-on (smaller in neutral); the asymmetric sleeve also in neutral."}</p>
      <div class="table-wrap"><table><thead><tr><th>Signal</th><th>Rising?</th><th class="num">Now</th><th class="num">Before</th><th>Data dated</th></tr></thead><tbody>
      ${row("Dollar liquidity (Fed balance sheet − reverse repo − Treasury account), 13 weeks", I.nl_rising, big(fin(I.net_liquidity) ? I.net_liquidity * 1e6 : null), big(fin(I.net_liquidity_13w_ago) ? I.net_liquidity_13w_ago * 1e6 : null), `${D.WALCL || "–"} · ${D.RRPONTSYD || "–"} · ${D.WTREGEN || "–"}`)}
      ${row("Stablecoins in circulation, 30 days", I.stables_rising, big(I.stablecoins), big(I.stablecoins_30d_ago), D.stablecoins || "–")}
      ${row("Bitcoin above its 200-day average", I.btc_above, fin(I.btc) ? `$${Math.round(I.btc).toLocaleString()}` : "–", fin(I.btc_sma200) ? `$${Math.round(I.btc_sma200).toLocaleString()} avg` : "–", D.btc || "–")}
      </tbody></table></div>`;
  }

  function tests() {
    const K = S.kill || {}, B = K.backtest || {};
    const rows = [];
    if (ENGINE === "hayes") {
      const a = K.confirmed_beats_btc_risk_on_180d || {}, b = K.asymmetric_3x_share || {};
      rows.push(["Confirmed entries in risk-on beat holding Bitcoin over 180 days (median)", a.pass, `${pct(a.median)} vs Bitcoin ${pct(a.btc_median)} (${a.n ?? 0} setups)`]);
      rows.push(["At least 10% of asymmetric setups reach 3x within a year", b.pass, `${share(b.share, 1)} (${b.n ?? 0} setups with a full year)`]);
      rows.push(["Walk-forward backtest", null, "Runs only after the history test above passes"]);
    } else {
      const c = K.coverage || {}, d = K.delisted_or_fell_80 || {}, x = K.reached_3x || {};
      rows.push(["Price history found for 80%+ of known crashes (delisted coins included)", c.pass, `${share(c.ratio)} measured (see the coverage limits below)`]);
      rows.push(["No more than 40% of setups end delisted or another 80% lower", d.pass, `${share(d.share, 1)} (${d.n ?? 0} setups)`]);
      rows.push(["At least 15% of setups reach 3x within a year", x.pass, `${share(x.share, 1)} (${x.n ?? 0} setups with a full year)`]);
      if (B.positive_windows) {
        rows.push(["Profit after costs in at least 2 of the 3 test periods", B.positive_windows.pass, `${B.positive_windows.count} of 3`]);
        rows.push(["No single coin supplies over half the profit", B.one_token_profit_share.pass, `${esc(B.one_token_profit_share.symbol || "–")}: ${share(B.one_token_profit_share.share)}`]);
      } else rows.push(["Walk-forward backtest", null, "Runs only after the history tests above pass"]);
    }
    $("testsBody").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Test (from the specification)</th><th>Result</th><th>Measured</th></tr></thead><tbody>
      ${rows.map(([t, ok, m]) => `<tr><td>${esc(t)}</td><td>${ok === null ? '<span class="pill calm">Not run</span>' : pass(ok)}</td><td class="small">${m}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function studyTable(es, ways) {
    const mults = ENGINE === "deep" ? [2, 3, 5, 10] : [2, 3, 5];
    const head = `<tr><th>Entry</th><th>Regime</th><th class="num">Setups</th><th class="num">30 days</th><th class="num">180 days (median)</th><th class="num">Worst 10% / best 10%</th><th class="num">Bitcoin, same days</th>${mults.map((m) => `<th class="num">Reached ${m}x</th>`).join("")}<th class="num">Fell ${ENGINE === "deep" ? "another 80%" : "another 50%"}</th><th class="num">Delisted or −80%</th></tr>`;
    const body = ways.filter((w) => es[w]).flatMap((w) => ["all", "risk_on", "neutral", "risk_off"].map((r) => {
      const s = es[w][r] || {}, h = s["180"] || {}, m = s["30"] || {};
      return `<tr${r === "all" ? ' class="sel"' : ""}><td>${r === "all" ? `<b>${esc(WAYS[w] || w)}</b>` : ""}</td><td>${r === "all" ? "Every regime" : esc((REGIME[r] || [r])[0])}</td><td class="num">${s.n ?? 0}</td><td class="num">${pct(m.median)}</td>
        <td class="num ${fin(h.median) ? (h.median >= 0 ? "up" : "down") : ""}">${pct(h.median)}</td><td class="num">${pct(h.p10)} / ${pct(h.p90)}</td><td class="num">${pct(h.btc_median)}</td>
        ${mults.map((k) => `<td class="num">${share(s[`reached_${k}x`])}</td>`).join("")}<td class="num">${share(ENGINE === "deep" ? s.fell_another_80 : s.fell_another_50)}</td><td class="num">${share(s.delisted_or_fell_80)}</td></tr>`;
    })).join("");
    return `<div class="table-wrap"><table><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
  }

  function study() {
    const es = S.event_study || {};
    const ways = ENGINE === "hayes" ? ["touch", "asymmetric", "confirmed"] : ["touch", "entry"];
    $("studyBody").innerHTML = `<p class="small">${es.coins ?? 0} coins since 2019 (${es.delisted_coins ?? 0} of them delisted since), ${es.events ?? 0} crashes into the band. Returns are from that day's close; "reached 3x" is the highest price within a year, so no real exit would catch all of it.${es.regime_inputs_ok === false ? " <b>Regime data failed to load: the regime split is not valid this week.</b>" : ""}</p>
      ${studyTable(es, ways)}
      ${S.context_75_92 ? `<details><summary>For context: the same statistics for coins 75–92% down (Engine A's territory)</summary>${studyTable(S.context_75_92, ["touch", "entry"])}</details>` : ""}
      ${(es.samples || []).length ? `<details><summary>The latest ${es.samples.length} crashes in the band</summary><div class="table-wrap"><table><thead><tr><th>Coin</th><th>Touched the band</th><th class="num">Trough</th><th>Regime</th><th class="num">180 days later</th><th class="num">Best within a year</th><th>Entry rule passed</th></tr></thead><tbody>
        ${es.samples.slice().reverse().map((x) => `<tr><td><b>${esc(x.symbol)}</b></td><td class="small">${esc(x.touch)}</td><td class="num">${pct(x.trough_dd)}</td><td>${esc((REGIME[x.regime] || [x.regime])[0])}</td><td class="num">${pct(x.ret_180)}</td><td class="num">${fin(x.max_mult) ? `${x.max_mult.toFixed(1)}x` : "–"}</td><td class="small">${esc(x.entry_day || x.confirmed_day || x.asymmetric_day || "no")}</td></tr>`).join("")}</tbody></table></div></details>` : ""}`;
  }

  function coverage() {
    const c = S.coverage; if (!c || !$("coverageBody")) return;
    $("coverageBody").innerHTML = `<p>${c.known_coins} coins known (${Object.entries(c.sources || {}).map(([k, v]) => `${v} listed on ${esc(k[0].toUpperCase() + k.slice(1))}`).join(", ")}, live or delisted); price history found for <b>${c.covered_coins}</b>. Crashes found: ${c.entries_found}; estimated missing: ${c.entries_estimated_missing}. Measured coverage <b>${share(c.ratio)}</b> (needs ${share(c.min_ratio)}).</p>
      <p class="small"><b>Why the true figure is lower:</b></p><ul class="small">${(c.known_gaps || []).map((g) => `<li>${esc(g)}</li>`).join("")}</ul>`;
  }

  function backtest() {
    const B = S.backtest; if (!$("backtestBody")) return;
    if (!B) { $("backtestBody").innerHTML = '<p class="muted">Not run: the backtest only runs after the history tests pass.</p>'; return; }
    const win = (bt) => `<div class="table-wrap"><table><thead><tr><th>Period</th><th class="num">Trades</th><th class="num">Average after costs</th><th class="num">Median</th><th class="num">Won</th><th class="num">Avg win / loss</th><th class="num">Bitcoin held</th></tr></thead><tbody>
      ${bt.windows.map((w) => `<tr><td>${esc(w.from.slice(0, 4))}–${esc(w.to === "now" ? "now" : w.to.slice(0, 4))}</td><td class="num">${w.n}</td><td class="num ${fin(w.expectancy) ? (w.expectancy > 0 ? "up" : "down") : ""}">${pct(w.expectancy, 1)}</td><td class="num">${pct(w.median, 1)}</td><td class="num">${share(w.hit_rate)}</td><td class="num">${pct(w.avg_win)} / ${pct(w.avg_loss)}</td><td class="num">${pct((B.btc_hold || {})[w.from])}</td></tr>`).join("")}</tbody></table></div>`;
    $("backtestBody").innerHTML = `<p class="small">Each trade buys 0.5% of the account at the close on the day every entry rule passed, pays 0.1% fees and slippage each way (doubled for thin volume), and follows the exit plan day by day: sell half at 2x, 15% at 4x and 15% at 8x; a trailing stop 40% below the best close (25% in risk-off); a disaster stop 25% under the trough; out after 270 days unless up 50%.</p>
      <h4>As specified (risk-on entries only): ${B.main.trades} trades</h4>${win(B.main)}
      <h4>Sensitivity: entering in every regime: ${B.every_regime.trades} trades</h4>${win(B.every_regime)}
      <p class="small muted">The every-regime version is shown only as the sensitivity test the specification asks for. Choosing it now, after seeing its result, would be fitting the rules to the past, so it is not used.</p>`;
  }

  async function load() {
    try {
      const r = await fetch(`${DATA}${ENGINE}/research.json?t=${Date.now()}`, { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      S = await r.json();
      summary(); regime(); tests(); study(); coverage(); backtest();
    } catch {
      $("summary").className = "banner warn";
      $("summary").innerHTML = "<b>This engine's report isn't available yet.</b> It is produced every week by the project's GitHub Actions; the first run starts shortly after the page goes live.";
    }
  }
  load();
})();
