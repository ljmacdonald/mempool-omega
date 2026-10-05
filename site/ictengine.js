/* ICT setups in the browser: a line-by-line port of ict/engine.py (tests/test_ict.py checks they agree).
   See ict/engine.py for the rules in plain words. */
(function (root) {
  "use strict";
  const P = { piv: 2, struct_look: 60, liq_look: 96, reclaim: 3, disp_atr: 1.0, fvg_min_atr: 0.1, stop_atr: 0.1, max_sweep_atr: 2.0,
    min_rr: 2.0, max_rr: 6.0, eq_atr: 0.1, range_look: 96, tgt_look: 192, fill_bars: 16, hold_bars: 96, min_risk_cost: 1.0, smt_look: 24 };
  const FACTORS = ["htf", "discount", "ote", "ob", "killzone", "silver", "smt", "major", "midnight"];
  const LEVEL_RANK = { "previous day low": 4, "Asian session low": 3, "equal lows": 2, "recent swing low": 1 };
  const FLIP = { "previous day low": "previous day high", "Asian session low": "Asian session high", "equal lows": "equal highs",
    "recent swing low": "recent swing high", "previous day high": "previous day low", "equal highs": "equal lows",
    "recent swing high": "recent swing low", "2x risk": "2x risk" };
  const isNaNum = (x) => typeof x !== "number" || Number.isNaN(x);
  const maxOf = (arr, a, b) => { let m = -Infinity; for (let i = a; i < b; i++) if (arr[i] > m) m = arr[i]; return m; };
  const minOf = (arr, a, b) => { let m = Infinity; for (let i = a; i < b; i++) if (arr[i] < m) m = arr[i]; return m; };

  const NYF = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
  function nyParts(ms) {
    const p = {}; for (const x of NYF.formatToParts(new Date(ms))) p[x.type] = x.value;
    return { day: +p.year * 10000 + +p.month * 100 + +p.day, mins: (+p.hour % 24) * 60 + +p.minute };
  }
  // c: {t:[ms], open:[], high:[], low:[], close:[]}
  function arrays(c) {
    const n = c.t.length; const day = new Array(n), mins = new Array(n);
    for (let i = 0; i < n; i++) { const q = nyParts(c.t[i]); day[i] = q.day; mins[i] = q.mins; }
    return { t: c.t.slice(), o: c.open.slice(), h: c.high.slice(), l: c.low.slice(), c: c.close.slice(), day, mins };
  }
  function flip(a) { return { ...a, o: a.o.map((x) => -x), h: a.l.map((x) => -x), l: a.h.map((x) => -x), c: a.c.map((x) => -x) }; }

  function atr(a, n = 14) {
    const { h, l, c } = a; const N = h.length; const tr = new Array(N); const out = new Array(N).fill(NaN);
    for (let i = 0; i < N; i++) tr[i] = i === 0 ? h[0] - l[0] : Math.max(h[i] - l[i], Math.abs(h[i] - c[i - 1]), Math.abs(l[i] - c[i - 1]));
    for (let i = n - 1; i < N; i++) { let s = 0; for (let j = i - n + 1; j <= i; j++) s += tr[j]; out[i] = s / n; }
    return out;
  }
  function pivots(a, k) {
    const { h, l } = a; const n = h.length; const ph = new Array(n).fill(false), pl = new Array(n).fill(false);
    for (let j = k; j < n - k; j++) {
      let hm = -Infinity, lm = Infinity;
      for (let q = j - k; q <= j + k; q++) if (q !== j) { if (h[q] > hm) hm = h[q]; if (l[q] < lm) lm = l[q]; }
      ph[j] = h[j] > hm; pl[j] = l[j] < lm;
    }
    return { ph, pl };
  }
  function sessions(a) {
    const n = a.t.length; const nan = () => new Array(n).fill(NaN);
    const pdh = nan(), pdl = nan(), ah = nan(), al = nan(), mo = nan(); const days = [];
    for (let i = 0; i < n; i++) { if (!days.length || days[days.length - 1][0] !== a.day[i]) days.push([a.day[i], i, i]); days[days.length - 1][2] = i; }
    for (let d = 0; d < days.length; d++) {
      const [, s, e] = days[d];
      if (d > 0) {
        const [, ps, pe] = days[d - 1]; const H = maxOf(a.h, ps, pe + 1), L = minOf(a.l, ps, pe + 1);
        let aH = -Infinity, aL = Infinity, any = false;
        for (let i = ps; i <= pe; i++) if (a.mins[i] >= 1200) { any = true; if (a.h[i] > aH) aH = a.h[i]; if (a.l[i] < aL) aL = a.l[i]; }
        for (let i = s; i <= e; i++) { pdh[i] = H; pdl[i] = L; if (any) { ah[i] = aH; al[i] = aL; } }
      }
      for (let i = s; i <= e; i++) mo[i] = a.o[s];
    }
    return { pdh, pdl, ah, al, mo };
  }
  function htfBias(a1, t15, barMs = 900000) {
    const out = new Array(t15.length).fill(0);
    if (!a1 || a1.t.length < 10) return out;
    const k = 2; const { ph, pl } = pivots(a1, k); const n = a1.t.length; const bias = new Array(n).fill(0);
    let lastH = NaN, lastL = NaN, b = 0;
    for (let i = 0; i < n; i++) {
      const j = i - k;
      if (j >= 0 && ph[j]) lastH = a1.h[j];
      if (j >= 0 && pl[j]) lastL = a1.l[j];
      if (!Number.isNaN(lastH) && a1.c[i] > lastH) { b = 1; lastH = NaN; } else if (!Number.isNaN(lastL) && a1.c[i] < lastL) { b = -1; lastL = NaN; }
      bias[i] = b;
    }
    let j = 0, cur = 0;
    for (let i = 0; i < t15.length; i++) { while (j < n && a1.t[j] + 3600000 <= t15[i] + barMs) { cur = bias[j]; j++; } out[i] = cur; }
    return out;
  }
  function align(a, b) { if (!b) return null; const pos = new Map(b.t.map((t, i) => [t, i])); return a.t.map((t) => (pos.has(t) ? pos.get(t) : -1)); }
  function zoneName(m) { return m >= 120 && m < 300 ? "London open" : m >= 420 && m < 600 ? "New York open" : m >= 600 && m < 720 ? "London close" : ""; }

  function detectBuys(a, a1, corr, cost = 0, start = 0, pp = {}) {
    const p = { ...P, ...pp }; const { h, l, o, c } = a; const n = h.length; const k = p.piv;
    const { ph, pl } = pivots(a, k); const at = atr(a); const ses = sessions(a); const bias = htfBias(a1, a.t); const cpos = align(a, corr);
    const out = [];
    for (let m = Math.max(start, 30); m < n; m++) {
      if (Number.isNaN(at[m])) continue;
      let j = -1;
      for (let q = m - 1 - k; q >= Math.max(m - p.struct_look, k); q--) if (ph[q]) { j = q; break; }
      if (j < 0) continue;
      const H = h[j];
      if (!(c[m] > H && c[m - 1] <= H)) continue;
      if (m <= j + 1) continue;
      let s = j + 1; for (let q = j + 1; q < m; q++) if (l[q] < l[s]) s = q;
      const slPx = l[s]; const atrS = !Number.isNaN(at[s]) ? at[s] : at[m];
      const reclaimEnd = Math.min(s + p.reclaim, m); const reclaimPx = maxOf(c, s, reclaimEnd + 1);
      const cands = [];
      for (const [name, lv] of [["previous day low", ses.pdl[s]], ["Asian session low", ses.al[s]]]) {
        if (!Number.isNaN(lv) && slPx < lv && lv < reclaimPx && lv - slPx <= p.max_sweep_atr * atrS) cands.push([LEVEL_RANK[name], name, lv]);
      }
      for (let q = s - 1 - k; q >= Math.max(s - p.liq_look, k); q--) {
        if (pl[q] && slPx < l[q] && l[q] < reclaimPx && l[q] - slPx <= p.max_sweep_atr * atrS && (q + 1 >= s || minOf(l, q + 1, s) >= l[q])) {
          let eq = false;
          for (let r = Math.max(s - p.liq_look, k); r < s - k; r++) if (r !== q && pl[r] && Math.abs(l[r] - l[q]) <= p.eq_atr * atrS) { eq = true; break; }
          const name = eq ? "equal lows" : "recent swing low"; cands.push([LEVEL_RANK[name], name, l[q]]); break;
        }
      }
      if (!cands.length) continue;
      cands.sort((x, y) => y[0] - x[0]);
      const [, lvlName, lvl] = cands[0];
      let disp = -Infinity; for (let q = s + 1; q <= m; q++) if (c[q] - o[q] > disp) disp = c[q] - o[q];
      if (disp < p.disp_atr * at[m]) continue;
      let best = null;
      for (let q = s + 2; q <= m; q++) { const gap = l[q] - h[q - 2]; if (gap >= p.fvg_min_atr * at[m] && (best === null || gap > best[0])) best = [gap, q]; }
      if (best === null) continue;
      const fq = best[1]; const fTop = l[fq], fBot = h[fq - 2];
      const entry = (fTop + fBot) / 2; const stop = slPx - p.stop_atr * atrS; const risk = entry - stop;
      if (risk <= 0 || risk < p.min_risk_cost * cost * Math.abs(entry)) continue;
      let tg = [];
      if (!Number.isNaN(ses.pdh[m]) && ses.pdh[m] > maxOf(h, s, m + 1)) tg.push([ses.pdh[m], "previous day high"]);
      for (let q = m - k; q >= Math.max(m - p.tgt_look, k); q--) {
        if (ph[q] && maxOf(h, q + 1, m + 1) < h[q]) {
          let eq = false;
          for (let r = Math.max(m - p.tgt_look, k); r < m - k + 1; r++) if (r !== q && ph[r] && Math.abs(h[r] - h[q]) <= p.eq_atr * at[m]) { eq = true; break; }
          tg.push([h[q], eq ? "equal highs" : "recent swing high"]);
        }
      }
      tg = tg.filter((x) => x[0] >= entry + p.min_rr * risk).sort((x, y) => x[0] - y[0] || (x[1] < y[1] ? -1 : x[1] > y[1] ? 1 : 0));
      let target, tName;
      if (tg.length && tg[0][0] <= entry + p.max_rr * risk) [target, tName] = tg[0]; else { target = entry + p.min_rr * risk; tName = "2x risk"; }
      const legHi = maxOf(h, s, m + 1); const zTop = legHi - 0.62 * (legHi - slPx), zBot = legHi - 0.79 * (legHi - slPx);
      let ob = false;
      for (let q = fq - 2; q >= Math.max(s - 2, 0); q--) if (c[q] < o[q]) { ob = l[q] <= fTop && h[q] >= fBot; break; }
      const loR = Math.max(0, m - p.range_look + 1); const mid = (maxOf(h, loR, m + 1) + minOf(l, loR, m + 1)) / 2;
      const mins = a.mins[m];
      let smt = false;
      if (cpos && s - p.smt_look >= 0) {
        const oursPrev = minOf(l, s - p.smt_look, s); const ci = []; for (let i = s - p.smt_look; i <= m; i++) ci.push(cpos[i]);
        if (ci.every((x) => x >= 0)) {
          let prevC = Infinity, nowC = Infinity;
          ci.forEach((x, idx) => { if (idx < p.smt_look) prevC = Math.min(prevC, corr.l[x]); else nowC = Math.min(nowC, corr.l[x]); });
          smt = slPx < oursPrev && nowC > prevC;
        }
      }
      const conf = { htf: bias[m] > 0, discount: entry < mid, ote: fBot <= zTop && fTop >= zBot, ob,
        killzone: (mins >= 120 && mins < 300) || (mins >= 420 && mins < 600) || (mins >= 600 && mins < 720),
        silver: (mins >= 180 && mins < 240) || (mins >= 600 && mins < 660) || (mins >= 840 && mins < 900),
        smt, major: lvlName !== "recent swing low", midnight: !Number.isNaN(ses.mo[m]) && entry < ses.mo[m] };
      out.push({ m, s, t: a.t[m], level: lvlName, level_px: lvl, sweep: slPx, fvg_top: fTop, fvg_bot: fBot, entry, stop, target, target_name: tName,
        rr: (target - entry) / risk, risk_pct: risk / Math.abs(entry), cost_r: cost * Math.abs(entry) / risk, conf,
        count: FACTORS.filter((f) => conf[f]).length, killzone: zoneName(mins) });
    }
    return out;
  }

  function simulate(a, s, pp = {}) {
    const p = { ...P, ...pp }; const { h, l, c } = a; const n = h.length; const { m, entry: e, stop: st, target: tg } = s; const risk = e - st;
    let f = -1;
    for (let t = m + 1; t < Math.min(n, m + 1 + p.fill_bars); t++) {
      if (l[t] <= e) { f = t; break; }
      if (h[t] >= tg) return { status: "missed", fill: -1, exit: t, r: 0 };
    }
    if (f < 0) return { status: m + p.fill_bars >= n ? "pending" : "expired", fill: -1, exit: -1, r: 0 };
    for (let t = f; t < Math.min(n, f + p.hold_bars); t++) {
      if (l[t] <= st) return { status: "loss", fill: f, exit: t, r: -1 - s.cost_r };
      if (t > f && h[t] >= tg) return { status: "win", fill: f, exit: t, r: s.rr - s.cost_r };
    }
    if (f + p.hold_bars > n) return { status: "active", fill: f, exit: -1, r: (c[n - 1] - e) / risk - s.cost_r };
    const t = f + p.hold_bars - 1;
    return { status: "time", fill: f, exit: t, r: (c[t] - e) / risk - s.cost_r };
  }

  function setups(c15, c1h, corr15, cost = 0, start = 0) {
    const a = arrays(c15); const a1 = c1h && c1h.t.length ? arrays(c1h) : null; const cb = corr15 && corr15.t.length ? arrays(corr15) : null;
    const out = [];
    for (const [side, aa, hh, cc] of [["buy", a, a1, cb], ["sell", flip(a), a1 ? flip(a1) : null, cb ? flip(cb) : null]]) {
      for (const s of detectBuys(aa, hh, cc, cost, start)) {
        const r = simulate(aa, s);
        if (side === "sell") {
          for (const kk of ["level_px", "sweep", "fvg_top", "fvg_bot", "entry", "stop", "target"]) s[kk] = -s[kk];
          [s.fvg_top, s.fvg_bot] = [s.fvg_bot, s.fvg_top]; s.level = FLIP[s.level]; s.target_name = FLIP[s.target_name];
        }
        out.push({ ...s, side, ...r });
      }
    }
    return out.sort((x, y) => x.m - y.m);
  }

  const api = { P, FACTORS, arrays, flip, atr, pivots, sessions, htfBias, detectBuys, simulate, setups, zoneName, nyParts };
  root.OmegaICT = api;
  if (typeof module !== "undefined") module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
