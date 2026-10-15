/* Shared by the Exchange coins, Small coins and ICT pages: the fake-signal checks on live order books and trades
   (spoofing, thin book, wash trading, price impact, other exchanges, whales, engineered moves) and the "where to buy it
   cheapest" comparison of exchanges and DEX pools with every fee. Moved here unchanged from site/app.js. */
(function (root) {
  "use strict";
  // deps: getJSON, store, setStatus, adaptive(), livePrice(symbol), pool, sleep, coinName, price, usd, esc, BINANCE
  function create(deps) {
    const E = root.OmegaEngine;
    const { getJSON, store, setStatus, adaptive, livePrice, pool, sleep, coinName, price, usd, esc, BINANCE } = deps;
    let venuesCache = null, venuesAt = 0;
    // ------------------------------------------------------------------ fake-signal checks (live data)
    async function venues() {
      if (venuesCache && Date.now() - venuesAt < 60000) return venuesCache;
      const out = {};
      const add = (base, v) => { (out[base] = out[base] || []).push(v); };
      try {
        const okx = await getJSON("https://www.okx.com/api/v5/market/tickers?instType=SPOT");
        for (const t of okx.data || []) if (t.instId.endsWith("-USDT") && +t.open24h > 0 && +t.last > 0) add(t.instId.slice(0, -5), { venue: "OKX", price: +t.last, change_pct: (+t.last / +t.open24h - 1) * 100 });
      } catch { /* OKX unavailable */ }
      try {
        const gate = await getJSON("https://api.gateio.ws/api/v4/spot/tickers");
        for (const t of gate) if (t.currency_pair.endsWith("_USDT") && +t.last > 0 && t.change_percentage !== "" && t.change_percentage != null) add(t.currency_pair.slice(0, -5), { venue: "Gate.io", price: +t.last, change_pct: +t.change_percentage });
      } catch { /* Gate unavailable */ }
      venuesCache = out; venuesAt = Date.now();
      return out;
    }
    const book = async (s) => { const d = await getJSON(`${BINANCE}/depth?symbol=${s}&limit=100`); return { bids: d.bids.map(([p, q]) => [+p, +q]), asks: d.asks.map(([p, q]) => [+p, +q]) }; };
    const trades = async (s) => (await getJSON(`${BINANCE}/trades?symbol=${s}&limit=1000`)).map((t) => ({ price: +t.price, qty: +t.qty, buyer_maker: !!t.isBuyerMaker, time: +t.time }));

    // Private per-device seed, rotated every hour: thresholds and snapshot timing are unknowable from outside.
    function deviceSeed() {
      let s0 = store.get("omega.seed", null);
      if (!Number.isInteger(s0)) { const a = new Uint32Array(1); crypto.getRandomValues(a); s0 = a[0]; store.set("omega.seed", s0); }
      return (s0 ^ Math.floor(Date.now() / 3600000)) >>> 0;
    }
    async function integrityFor(ideas, bySym, uni) {
      const symbols = ideas.map((d) => d.symbol);
      const seed = deviceSeed(); const t = E.thresholds(seed); const gaps = E.snapshotGaps(E.mulberry32((seed ^ 0x9E3779B9) >>> 0));
      const [ven, b1] = await Promise.all([venues(), pool(symbols, 6, book)]);
      const snaps = [b1];
      for (let g = 0; g < gaps.length; g++) {
        setStatus(`Fake-signal checks: order-book snapshot ${g + 2} of 3…`);
        await sleep(gaps[g] * 1000);
        snaps.push(await pool(symbols, 6, book));
      }
      const tr = await pool(symbols, 6, trades);
      const u = Object.fromEntries(uni.map((x) => [x.symbol, x]));
      const out = {};
      symbols.forEach((s, i) => {
        const checks = [];
        const books = snaps.map((sn) => sn[i]).filter((b) => b && b.bids.length && b.asks.length);
        if (books.length >= 2) checks.push(E.checkWalls(books, t), E.checkThinBook(books[0], t));
        if (tr[i]) checks.push(E.checkWash(tr[i], t));
        if (bySym[s]) checks.push(E.checkImpact(bySym[s], t));
        if (u[s]) checks.push(E.checkVenues(u[s].lastPrice, u[s].change, ven[coinName(s)] || [], t));
        if (bySym[s]) checks.push(E.checkWhale(bySym[s], t));
        if (ideas[i].features) checks.push(E.checkEngineered(ideas[i].features, t));
        out[s] = E.combineChecks(checks, (adaptive() || {}).check_penalties);
      });
      return out;
    }

    // ------------------------------------------------------------------ where to buy (all-in costs)
    const VENUE_SYM = {
      binance: (b) => `${b}USDT`, okx: (b) => `${b}-USDT`, bitget: (b) => `${b}USDT`, gate: (b) => `${b}_USDT`,
      htx: (b) => `${b.toLowerCase()}usdt`, kraken: (b) => `${b === "BTC" ? "XBT" : b}USD`, coinbase: (b) => `${b}-USD`,
    };
    const VENUE_LINK = {
      binance: (b) => `https://www.binance.com/en/trade/${b}_USDT`, okx: (b) => `https://www.okx.com/trade-spot/${b.toLowerCase()}-usdt`,
      bitget: (b) => `https://www.bitget.com/spot/${b}USDT`, gate: (b) => `https://www.gate.io/trade/${b}_USDT`,
      htx: (b) => `https://www.htx.com/trade/${b.toLowerCase()}_usdt`, kraken: (b) => `https://pro.kraken.com/app/trade/${b.toLowerCase()}-usd`,
      coinbase: (b) => `https://www.coinbase.com/advanced-trade/spot/${b}-USD`,
    };
    const num2 = (rows) => rows.map((r) => [+r[0], +r[1]]).filter(([p, q]) => p > 0 && q > 0);
    const BOOK = {
      binance: async (s) => { const d = await getJSON(`${BINANCE}/depth?symbol=${s}&limit=100`); return { bids: num2(d.bids), asks: num2(d.asks) }; },
      okx: async (s) => { const d = (await getJSON(`https://www.okx.com/api/v5/market/books?instId=${s}&sz=100`)).data[0]; return { bids: num2(d.bids), asks: num2(d.asks) }; },
      bitget: async (s) => { const d = (await getJSON(`https://api.bitget.com/api/v2/spot/market/orderbook?symbol=${s}&limit=100`)).data; return { bids: num2(d.bids), asks: num2(d.asks) }; },
      gate: async (s) => { const d = await getJSON(`https://api.gateio.ws/api/v4/spot/order_book?currency_pair=${s}&limit=100`); return { bids: num2(d.bids), asks: num2(d.asks) }; },
      htx: async (s) => { const d = (await getJSON(`https://api.huobi.pro/market/depth?symbol=${s}&type=step0`)).tick; return { bids: num2(d.bids).slice(0, 100), asks: num2(d.asks).slice(0, 100) }; },
      kraken: async (s) => { const r = (await getJSON(`https://api.kraken.com/0/public/Depth?pair=${s}&count=100`)).result; const d = r[Object.keys(r)[0]]; return { bids: num2(d.bids), asks: num2(d.asks) }; },
      coinbase: async (s) => { const d = await getJSON(`https://api.exchange.coinbase.com/products/${s}/book?level=2`); return { bids: num2(d.bids).slice(0, 200), asks: num2(d.asks).slice(0, 200) }; },
    };
    const RPC = { ethereum: "https://ethereum-rpc.publicnode.com", bsc: "https://bsc-rpc.publicnode.com", base: "https://base-rpc.publicnode.com", arbitrum: "https://arbitrum-one-rpc.publicnode.com" };
    const MAJOR_QUOTES = new Set(["USDT", "USDC", "WETH", "ETH", "WBNB", "BNB", "SOL", "WSOL", "DAI", "USD1", "FDUSD", "USDE"]);
    const cache = {};
    async function cached(key, ttl, fn) { const c = cache[key]; if (c && Date.now() - c.at < ttl) return c.v; const v = await fn(); cache[key] = { at: Date.now(), v }; return v; }
    async function gasUsd(chain) {
      const c = E.CHAINS[chain]; if (c.gasUsd !== undefined) return c.gasUsd;
      return cached(`gas:${chain}`, 60000, async () => {
        const r = await fetch(RPC[chain], { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "eth_gasPrice", params: [] }) });
        const wei = parseInt((await r.json()).result, 16);
        const px = +(await getJSON(`${BINANCE}/ticker/price?symbol=${c.native}USDT`)).price;
        return wei * c.gasUnits / 1e18 * px;
      });
    }
    async function dexPools(base) {
      return cached(`dex:${base}`, 120000, async () => {
        const d = await getJSON(`https://api.dexscreener.com/latest/dex/search?q=${encodeURIComponent(base)}`);
        const seen = new Set(); const out = [];
        for (const p of (d.pairs || []).filter((p) => p.baseToken?.symbol?.toUpperCase() === base && E.CHAINS[p.chainId] && MAJOR_QUOTES.has((p.quoteToken?.symbol || "").toUpperCase()) && (p.liquidity?.usd || 0) >= 100000 && +p.priceUsd > 0)
          .sort((a, b) => b.liquidity.usd - a.liquidity.usd)) {
          const k = `${p.chainId}:${p.dexId}`; if (seen.has(k)) continue; seen.add(k); out.push(p); if (out.length >= 3) break;
        }
        return out;
      });
    }

    async function compareVenues(idea, amount) {
      const base = coinName(idea.symbol); const a = idea.anchor || idea;
      const refMid = livePrice(idea.symbol) ?? idea.price_now;
      const tasks = Object.keys(E.VENUES).map(async (v) => {
        try {
          const book = await cached(`book:${v}:${base}`, 30000, () => BOOK[v](VENUE_SYM[v](base)));
          if (!book.asks.length || !book.bids.length) return null;
          const f = E.VENUES[v]; const r = E.cexNet({ amount, asks: book.asks, bids: book.bids, taker: f.taker, withdraw_usd: f.withdraw_usd, refMid });
          if (Math.abs((book.asks[0][0] + book.bids[0][0]) / 2 / refMid - 1) > 0.2) return null;   // a different token with the same ticker
          return { kind: "CEX", name: f.name, link: VENUE_LINK[v](base), fee: f.taker, r, net: r.netAt(a.take_profit), loss: r.netAt(a.safety_exit),
            note: `${(f.taker * 100).toFixed(2)}% taker fee each way · ${usd(-f.withdraw_usd).replace("−", "")} to withdraw` + (r.filled ? "" : " · order book too thin for this amount") };
        } catch { return null; }
      });
      let pools = [];
      try { pools = await dexPools(base); } catch { /* DexScreener unavailable */ }
      const dexTasks = pools.map(async (p) => {
        try {
          const ch = E.CHAINS[p.chainId]; const g = await gasUsd(p.chainId);
          const fee = E.DEX_FEE[p.dexId] ?? 0.003;
          const args = { amount, priceUsd: +p.priceUsd, liqUsd: p.liquidity.usd, swapFee: fee, gasUsd: g, refMid };
          const r = E.dexNet({ ...args, mev: ch.mev }); const safe = E.dexNet({ ...args, mev: 0 });
          if (Math.abs(+p.priceUsd / refMid - 1) > 0.2) return null;
          const addr = p.baseToken.address;
          return { kind: "DEX", name: `${p.dexId} on ${ch.name}`, link: p.url, fee, r, net: r.netAt(a.take_profit), loss: r.netAt(a.safety_exit), safeNet: safe.netAt(a.take_profit),
            note: `${(fee * 100).toFixed(2)}% swap fee · ~${usd(-g).replace("−", "")} gas per swap · pool ${usd(-p.liquidity.usd).replace("−", "").replace(/\.\d+$/, "")} · token ${addr.slice(0, 6)}…${addr.slice(-4)} (check it's the real one) · front-running/sandwich bots could take ~${usd(-(safe.netAt(a.take_profit) - r.netAt(a.take_profit))).replace("−", "")}; use ${ch.protect} to avoid it` };
        } catch { return null; }
      });
      const rows = (await Promise.all(tasks.concat(dexTasks))).filter(Boolean).sort((x, y) => y.net - x.net);
      return { rows, amount, base };
    }

    function venueTable(cmp) {
      if (!cmp.rows.length) return `<p class="small muted">Couldn't load prices from other exchanges right now.</p>`;
      const best = cmp.rows.find((r) => r.r.filled !== false) || cmp.rows[0];
      return `<div class="banner ${best.net >= 0 ? "good" : "warn"}"><b>Best place for $${cmp.amount.toLocaleString()}: ${esc(best.name)}.</b> If it reaches the take profit you'd take out about <b>${usd(best.net)}</b> after every fee (trading fees, slippage, ${best.kind === "DEX" ? "gas and front-running risk" : "withdrawal"}). If it hits the safety exit: ${usd(best.loss)}.</div>
        <div class="table-wrap"><table><thead><tr><th>Where</th><th class="num">Money out at take profit</th><th class="num">At safety exit</th><th class="num">Break-even price</th><th>Costs included</th></tr></thead><tbody>
        ${cmp.rows.map((r) => `<tr><td><a href="${esc(r.link)}" target="_blank" rel="noopener">${esc(r.name)}</a> <span class="pill ${r.kind === "DEX" ? "warn" : "calm"}">${r.kind}</span></td>
          <td class="num ${r.net >= 0 ? "up" : "down"}">${usd(r.net)}</td><td class="num down">${usd(r.loss)}</td><td class="num">${price(r.r.breakEven)}</td>
          <td class="small" style="white-space:normal;min-width:16em">${esc(r.note)}${r.safeNet !== undefined ? ` (with protection: ${usd(r.safeNet)})` : ""}</td></tr>`).join("")}
        </tbody></table></div>
        <p class="small muted">Live order books from each exchange: your amount is "walked" through real orders to get the price you'd actually pay and receive. Fees are standard entry-level rates (yours may be lower with VIP tiers or fee-token discounts). Withdrawal = typical fee to move stablecoins off the exchange on a cheap network; turning them into bank money can add more. Using limit orders instead of market orders can cut fees and slippage, but they may not fill. DEX rows only list pools with at least $100,000 in them. Fake tokens often copy popular names, so check the token address on the exchange or a block explorer before buying.</p>`;
    }

    function checksBlock(it, pending) {
      if (pending) return `<p class="small muted">⏳ Fake-signal checks are running for this coin (order book read 3 times at random moments). The score updates when they finish.</p>`;
      if (!it) return `<p class="small muted">Fake-signal checks couldn't run for this coin right now.</p>`;
      const icon = (ok) => (ok === true ? `<span class="ok">✓</span>` : ok === false ? `<span class="bad">✗</span>` : `<span class="na">–</span>`);
      const label = it.checked ? `${it.passed} of ${it.checked} checks passed` : "Checks unavailable";
      const cls = it.penalty === 0 ? "good" : it.penalty < 0.1 ? "calm" : "warn";
      return `<details class="checks"><summary>Fake-signal check: <span class="pill ${cls}">${label}</span></summary>
        <ul>${it.checks.map((c) => `<li>${icon(c.ok)}<span>${esc(c.text)}</span></li>`).join("")}</ul>
        <p class="small muted">Checks: spoofing (fake orders that vanish), thin order book, fake back-and-forth trading, whether other exchanges (OKX, Gate.io) confirm the price, and whale-sized trades. Failed checks lower the score.</p></details>`;
    }
    return { venues, book, trades, deviceSeed, integrityFor, compareVenues, venueTable, checksBlock };
  }
  root.OmegaMarket = { create };
})(typeof window !== "undefined" ? window : globalThis);
