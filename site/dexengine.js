/* DEX engine: scam checks and all-in costs, the same rules as dex/security.py and dex/costs.py
   (tests/test_dex.py checks the two agree). Pure functions only. */
(function (root) {
  "use strict";
  const E = root.OmegaEngine || (typeof require !== "undefined" ? require("./engine.js") : null);

  const BASE = {
    min_liq: 500000, min_age_days: 14, min_active_days: 10, min_buyers: 200, min_sellers: 120, min_sell_ratio: 0.30,
    min_holders: 2000, max_tax: 0.05, max_top10: 0.50, max_top10_soft: 0.30, max_creator: 0.20, max_liq_drop_6h: 0.20,
    max_liq_drop_24h: 0.35, max_liq_jump_72h: 1.00, max_liq_jump_reject: 3.00, max_trades_per_trader: 8,
    max_churn: 15, max_insiders: 20,
  };
  const PEN = { top10: 0.10, holders: 0.05, lp_unverified: 0.05, lp_partial: 0.05, wash: 0.10, churn: 0.10, boosted: 0.10,
    metadata: 0.02, external_call: 0.05, liq_jump: 0.15, insiders: 0.10, creator_soft: 0.05 };
  const EVM_DEAD = new Set(["", "0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead"]);
  const CHAIN_SIM = { ethereum: true, bsc: true };
  const TRADER_SCALE = { ethereum: 0.5 };

  function thresholds(seed) {
    if (seed === null || seed === undefined) return { ...BASE };
    const rnd = E.mulberry32(seed); const out = {};
    for (const k of Object.keys(BASE)) { const r = rnd(); out[k] = k.startsWith("min_") ? BASE[k] * (1 + 0.3 * r) : BASE[k] * (1 - 0.25 * r); }
    out.min_active_days = Math.min(out.min_active_days, 14);
    return out;
  }

  // ------------------------------------------------------------------ normalise
  const b = (x) => { if (x && typeof x === "object" && !Array.isArray(x)) x = x.status; if (x === null || x === undefined || x === "") return null; return String(x) === "1"; };
  const num = (x) => { if (x === null || x === undefined || x === "" || typeof x === "boolean") return null; const v = Number(x); return Number.isFinite(v) ? v : null; };
  const mx = (...xs) => { const v = xs.filter((x) => x !== null && x !== undefined); return v.length ? Math.max(...v) : null; };
  const anyOf = (...xs) => { const v = xs.filter((x) => x !== null && x !== undefined); return v.length ? v.some(Boolean) : null; };
  const int0 = (x) => Math.trunc(num(x) || 0);

  function emptyFacts() {
    return { goplus: false, honeypot_src: null, rugcheck_src: null, honeypot: null, cannot_sell_all: null, cannot_buy: null, sim_ok: null,
      buy_tax: null, sell_tax: null, tax_modifiable: false, owner_active: false, hidden_owner: false, take_back_ownership: false,
      owner_change_balance: false, selfdestruct: false, proxy: false, open_source: null, mintable: false, pausable: false, blacklist: false,
      whitelist: false, cooldown: false, anti_whale_modifiable: false, external_call: false, mint_authority: false, freeze_authority: false,
      permanent_delegate: false, transfer_hook: false, non_transferable: false, default_frozen: false, closable: false, metadata_mutable: false,
      holder_count: null, top10_pct: null, creator_pct: null, insiders: null, lp_secured_pct: null, lp_unlock_soon: false,
      lp_top_unlocked_eoa_pct: null, lp_providers: null, same_creator_honeypots: 0, trusted: false, rugged: false };
  }

  function fromGoplusEvm(r, pools, nowS) {
    const f = emptyFacts(); if (!r || !Object.keys(r).length) return f;
    f.goplus = true;
    const owner = (r.owner_address || "").toLowerCase();
    f.hidden_owner = !!b(r.hidden_owner); f.take_back_ownership = !!b(r.can_take_back_ownership);
    f.owner_active = !EVM_DEAD.has(owner) || f.hidden_owner || f.take_back_ownership;
    f.honeypot = b(r.is_honeypot); f.cannot_sell_all = b(r.cannot_sell_all); f.cannot_buy = b(r.cannot_buy);
    f.buy_tax = num(r.buy_tax); f.sell_tax = num(r.sell_tax);
    f.tax_modifiable = !!(b(r.slippage_modifiable) || b(r.personal_slippage_modifiable));
    f.owner_change_balance = !!b(r.owner_change_balance); f.selfdestruct = !!b(r.selfdestruct); f.proxy = !!b(r.is_proxy);
    f.open_source = b(r.is_open_source); f.mintable = !!b(r.is_mintable); f.pausable = !!b(r.transfer_pausable);
    f.blacklist = !!b(r.is_blacklisted); f.whitelist = !!b(r.is_whitelisted); f.cooldown = !!b(r.trading_cooldown);
    f.anti_whale_modifiable = !!b(r.anti_whale_modifiable); f.external_call = !!b(r.external_call);
    f.same_creator_honeypots = int0(r.honeypot_with_same_creator);
    f.holder_count = int0(r.holder_count) || null;
    const cex = r.is_in_cex || {};
    f.trusted = !!(b(r.trust_list) || (cex && typeof cex === "object" && String(cex.listed) === "1"));
    f.creator_pct = mx(num(r.creator_percent), num(r.owner_percent));
    const skip = new Set([...(pools || []).map((p) => p.toLowerCase()), ...EVM_DEAD]);
    const top = (r.holders || []).filter((h) => !skip.has((h.address || "").toLowerCase()) && !int0(h.is_locked) && !h.tag);
    if (r.holders && r.holders.length) f.top10_pct = top.slice(0, 10).reduce((s, h) => s + (num(h.percent) || 0), 0);
    const lp = r.lp_holders || [];
    if (lp.length) {
      let secured = 0, topEoa = 0, soon = false;
      for (const h of lp) {
        const pct = num(h.percent) || 0; const addr = (h.address || "").toLowerCase(); const locked = !!int0(h.is_locked);
        if (locked) { const ends = (h.locked_detail || []).map((d) => num(d.end_time)).filter((e) => e); if (ends.length && Math.min(...ends) < nowS + 7 * 86400) soon = true; }
        if (EVM_DEAD.has(addr) || locked) secured += pct; else if (!int0(h.is_contract)) topEoa = Math.max(topEoa, pct);
      }
      const nft = lp.some((h) => h.NFT_list && h.NFT_list.length);
      f.lp_secured_pct = nft ? null : secured; f.lp_top_unlocked_eoa_pct = nft ? null : topEoa; f.lp_unlock_soon = soon;
      f.lp_providers = int0(r.lp_holder_count) || lp.length;
    }
    return f;
  }

  function fromGoplusSol(r) {
    const f = emptyFacts(); if (!r || !Object.keys(r).length) return f;
    f.goplus = true;
    f.mint_authority = !!b(r.mintable); f.freeze_authority = !!b(r.freezable); f.permanent_delegate = !!b(r.balance_mutable_authority);
    f.transfer_hook = (Array.isArray(r.transfer_hook) ? r.transfer_hook.length > 0 : !!r.transfer_hook && Object.keys(r.transfer_hook).length > 0) || !!b(r.transfer_hook_upgradable);
    f.non_transferable = !!b(r.non_transferable);
    f.default_frozen = String(r.default_account_state) === "2" || !!b(r.default_account_state_upgradable);
    f.closable = !!b(r.closable); f.metadata_mutable = !!b(r.metadata_mutable);
    const tf = r.transfer_fee || {};
    let rate = num(tf.current_fee_rate && typeof tf.current_fee_rate === "object" ? tf.current_fee_rate.fee_rate : tf.fee_rate);
    if (rate !== null) { rate = rate > 1 ? rate / 10000 : rate; f.buy_tax = f.sell_tax = rate; }
    f.tax_modifiable = !!b(r.transfer_fee_upgradable);
    f.owner_active = f.tax_modifiable || f.mint_authority || f.freeze_authority;
    f.holder_count = int0(r.holder_count) || null;
    f.trusted = String(r.trusted_token) === "1";
    const hs = (r.holders || []).filter((h) => !int0(h.is_locked) && !h.tag);
    if (r.holders && r.holders.length) f.top10_pct = hs.slice(0, 10).reduce((s, h) => s + (num(h.percent) || 0), 0);
    return f;
  }

  function addHoneypotIs(f0, d) {
    const f = { ...f0 }; if (d === null || d === undefined) return f;
    if (d._unsupported) { f.honeypot_src = "unsupported"; return f; }
    f.honeypot_src = true;
    const hp = (d.honeypotResult || {}).isHoneypot; const sim = d.simulationResult || {};
    f.honeypot = anyOf(f.honeypot, hp === undefined ? null : hp);
    f.sim_ok = !!d.simulationSuccess;
    const bt = num(sim.buyTax), st = num(sim.sellTax);
    f.buy_tax = mx(f.buy_tax, bt !== null ? bt / 100 : null); f.sell_tax = mx(f.sell_tax, st !== null ? st / 100 : null);
    if ((d.flags || []).some((x) => ["EXTREMELY_HIGH_TAXES", "high_fail_rate"].includes(x && typeof x === "object" ? x.flag : x))) f.honeypot = true;
    return f;
  }

  function addRugcheck(f0, d) {
    const f = { ...f0 }; if (d === null || d === undefined) return f;
    f.rugcheck_src = true;
    f.mint_authority = f.mint_authority || !!d.mintAuthority; f.freeze_authority = f.freeze_authority || !!d.freezeAuthority;
    const tfp = num((d.transferFee || {}).pct);
    if (tfp !== null) { f.buy_tax = mx(f.buy_tax, tfp / 100); f.sell_tax = mx(f.sell_tax, tfp / 100); }
    const ext = d.token_extensions || {};
    if (ext && typeof ext === "object" && !Array.isArray(ext)) { f.permanent_delegate = f.permanent_delegate || !!ext.permanentDelegate; f.transfer_hook = f.transfer_hook || !!ext.transferHook; }
    f.rugged = !!d.rugged; f.insiders = int0(d.graphInsidersDetected);
    const known = d.knownAccounts || {};
    const holders = (d.topHolders || []).filter((h) => !["AMM", "LOCKER"].includes((known[h.owner] || {}).type));
    if (d.topHolders && d.topHolders.length) f.top10_pct = holders.slice(0, 10).reduce((s, h) => s + (num(h.pct) || 0), 0) / 100;
    const locked = (d.markets || []).map((m) => num((m.lp || {}).lpLockedPct)).filter((x) => x !== null);
    if (locked.length) f.lp_secured_pct = Math.max(...locked) / 100;
    f.lp_providers = int0(d.totalLPProviders) || f.lp_providers;
    f.owner_active = f.owner_active || f.mint_authority || f.freeze_authority;
    return f;
  }

  // ------------------------------------------------------------------ assess
  const P0 = (x) => `${Math.round(x * 100)}%`;
  const P1 = (x) => `${(x * 100).toFixed(1)}%`;
  const M = (x) => `$${Math.round(x).toLocaleString("en-US")}`;
  function assess(f, m, t, chain) {
    const hard = [], checks = [];
    const rej = (key, text) => hard.push({ key, text });
    const chk = (key, ok, text, pen = 0) => checks.push({ key, ok, text, penalty: ok === false ? pen : 0 });
    const evm = chain !== "solana"; const owner = f.owner_active;
    if (!f.goplus) rej("unverified", "The security check couldn't run for this token, so it isn't suggested (we never guess).");
    if (evm && CHAIN_SIM[chain] && !f.honeypot_src) rej("unverified", "The buy-and-sell test couldn't run for this token, so it isn't suggested.");
    if (chain === "solana" && !f.rugcheck_src) rej("unverified", "The Solana token check (RugCheck) couldn't run, so it isn't suggested.");
    if (f.honeypot || f.cannot_sell_all || f.cannot_buy || f.sim_ok === false) rej("honeypot", "Honeypot: a test buy-and-sell failed or the token blocks selling. You could buy but not sell.");
    else chk("sell_test", f.sim_ok ? true : null, f.sim_ok ? "A simulated buy and sell worked." : "No sell simulation is possible for this pool type, so stricter real-seller rules apply instead (below).");
    const sellers = m.sellers_h24 || 0, buyers = m.buyers_h24 || 0;
    const strict = f.sim_ok ? 1.0 : 1.5; const minRatio = t.min_sell_ratio + (f.sim_ok ? 0 : 0.10);
    const scale = TRADER_SCALE[chain] || 1.0;
    if (buyers < t.min_buyers * scale || sellers < t.min_sellers * strict * scale) rej("few_traders", `Too few real traders: ${buyers} wallets bought and ${sellers} sold in the last 24 hours.`);
    else if (sellers / Math.max(buyers, 1) < minRatio) rej("sell_block", `Only ${sellers} wallets sold against ${buyers} that bought in 24 hours. Contracts that let checkers sell but block ordinary buyers look exactly like this.`);
    else chk("real_sellers", true, `${sellers} different wallets really sold in the last 24 hours, so ordinary people can get out.`);
    const tax = mx(f.buy_tax, f.sell_tax);
    if (tax !== null && tax > t.max_tax) rej("tax", `Tax of ${P0(tax)} on buying or selling. That eats most short-term gains.`);
    if (f.tax_modifiable && owner) rej("tax_modifiable", "The owner can raise the tax at any time, even to 100% after you buy.");
    if (!hard.some((h) => h.key === "tax" || h.key === "tax_modifiable")) chk("tax", true, `Tax: ${P1(tax || 0)} (counted in your costs), and it can't be raised.`);
    if (f.hidden_owner || f.take_back_ownership) rej("hidden_owner", "Has a hidden owner or a way to take back ownership after 'giving it up'.");
    if (f.owner_change_balance && owner) rej("balance_change", "The owner can change wallet balances, i.e. take your tokens.");
    if (f.selfdestruct) rej("selfdestruct", "The contract can destroy itself.");
    if (f.proxy && !f.trusted) rej("upgradeable", "The code can be replaced after our checks (upgradeable contract).");
    if (evm && f.open_source === false) rej("closed_source", "The contract code is secret, so what it does can't be checked.");
    const powers = [["mintable", "print new tokens"], ["pausable", "pause trading"], ["blacklist", "block wallets from selling"], ["whitelist", "let only chosen wallets trade"],
      ["cooldown", "add selling delays"], ["anti_whale_modifiable", "limit how much you can sell"]].filter(([k]) => f[k]).map(([, n]) => n);
    if (powers.length && owner) rej("owner_powers", `The owner can still ${powers.join(", ")}. Rejected while anyone holds those powers.`);
    if (f.external_call) { if (owner) rej("external_call", "The contract hands decisions to another contract the owner controls."); else chk("external_call", false, "The contract relies on another contract, so its behaviour can't be fully checked.", PEN.external_call); }
    const solBad = [["mint_authority", "print new tokens"], ["freeze_authority", "freeze your tokens"], ["permanent_delegate", "move tokens out of your wallet"],
      ["transfer_hook", "run hidden code on every transfer"], ["non_transferable", "make tokens non-transferable"], ["default_frozen", "freeze new wallets by default"], ["closable", "close the token"]].filter(([k]) => f[k]).map(([, n]) => n);
    if (solBad.length) rej("authorities", `Someone can still ${solBad.join(", ")}.`);
    if (!hard.some((h) => ["hidden_owner", "balance_change", "selfdestruct", "upgradeable", "closed_source", "owner_powers", "external_call", "authorities"].includes(h.key)))
      chk("owner", true, owner === false ? "Nobody can print tokens, freeze wallets, block sales or change the code." : "An owner exists but holds none of the dangerous powers.");
    if (f.metadata_mutable) chk("metadata", false, "Name and logo can still be changed (used to impersonate other tokens).", PEN.metadata);
    if (f.same_creator_honeypots > 0) rej("serial_scammer", `The same creator made ${f.same_creator_honeypots} known scam token(s) before.`);
    if (f.rugged) rej("rugged", "RugCheck marks this token as already rugged.");
    const liq = m.liq_real || 0;
    if (liq < t.min_liq) rej("liquidity", `Only ${M(liq)} of real money in the pool. The minimum is ${M(BASE.min_liq)}, raised by a secret amount that changes every hour.`);
    else chk("liquidity", true, `${M(liq)} of real money in the pool (SOL/ETH/BNB or stablecoins only).`);
    const age = m.age_days || 0;
    if (age < t.min_age_days) rej("young", `The pool is only ${Math.round(age)} days old. The minimum is ${BASE.min_age_days} days, raised by a secret amount that changes every hour.`);
    const act = m.active_days;
    if (act !== null && act !== undefined && act < t.min_active_days) rej("revived", `Real trading on only ${act} of the last 14 days. Old, quiet tokens get 'revived' to look established before a dump.`);
    else if (act !== null && act !== undefined && age >= t.min_age_days) chk("history", true, `${Math.round(age)} days old and actively traded on ${act} of the last 14 days.`);
    const d6 = m.liq_change_6h, d24 = m.liq_change_24h, j72 = m.liq_change_72h;
    const has = (x) => x !== null && x !== undefined;
    if (has(d6) && d6 < -t.max_liq_drop_6h) rej("liq_pull", `Pool money fell ${P0(-d6)} in the last 6 hours: liquidity is being pulled.`);
    else if (has(d24) && d24 < -t.max_liq_drop_24h) rej("liq_pull", `Pool money fell ${P0(-d24)} in 24 hours.`);
    if (has(j72) && j72 > t.max_liq_jump_reject) rej("liq_added", `Pool money grew ${P0(j72)} in 3 days. Money added just before a pump is usually pulled after it.`);
    else if (has(j72) && j72 > t.max_liq_jump_72h) chk("liq_jump", false, `Pool money grew ${P0(j72)} in 3 days; it may be temporary.`, PEN.liq_jump);
    if (f.lp_unlock_soon) rej("lp_unlock", "The locked pool money unlocks within a week.");
    if (f.lp_top_unlocked_eoa_pct !== null && f.lp_top_unlocked_eoa_pct > 0.5) rej("lp_owner", `One ordinary wallet owns ${P0(f.lp_top_unlocked_eoa_pct)} of the pool and can withdraw it.`);
    if (f.lp_secured_pct === null) chk("lp", false, "The pool's money isn't locked (normal for modern pools). This app watches it live and warns you if it's pulled.", PEN.lp_unverified);
    else if (f.lp_secured_pct < 0.9) chk("lp", false, `Only ${P0(f.lp_secured_pct)} of the pool's money is locked or burned.`, PEN.lp_partial);
    else chk("lp", true, `${P0(f.lp_secured_pct)} of the pool's money is locked or burned.`);
    const top10 = f.top10_pct;
    if (top10 !== null && top10 > t.max_top10 && !f.trusted) rej("concentrated", `The 10 biggest wallets hold ${P0(top10)}. They can crash the price at will.`);
    else if (top10 !== null && top10 > t.max_top10_soft) chk("holders", false, `The 10 biggest wallets hold ${P0(top10)}.`, PEN.top10);
    else if (top10 !== null) chk("holders", true, `The 10 biggest wallets hold ${P0(top10)}.`);
    const cp = f.creator_pct;
    if (cp !== null && cp > t.max_creator) rej("creator", `The creator/owner still holds ${P0(cp)}.`);
    else if (cp !== null && cp > 0.05) chk("creator", false, `The creator/owner still holds ${P0(cp)}.`, PEN.creator_soft);
    const hc = f.holder_count;
    if (hc !== null && hc < t.min_holders) chk("holder_count", false, `Only ${hc.toLocaleString("en-US")} holders. A few wallets dressed up as many is a common trick.`, PEN.holders);
    const ins = f.insiders;
    if (ins !== null && ins > t.max_insiders) rej("insiders", `${ins} linked 'insider' wallets (sniper or team bundle) were detected.`);
    else if (ins) chk("insiders", false, `${ins} linked 'insider' wallets detected (bought together, may sell together).`, PEN.insiders);
    const trades = (m.buys_h24 || 0) + (m.sells_h24 || 0), traders = buyers + sellers;
    if (traders && trades / traders > t.max_trades_per_trader) chk("wash", false, `About ${Math.round(trades / traders)} trades per wallet in 24 hours: bots trading with themselves to fake activity.`, PEN.wash);
    else if (traders) chk("wash", true, "Trading comes from many different wallets, not a few bots.");
    if (liq && (m.vol_h24 || 0) / liq > t.max_churn) chk("churn", false, `Daily trading is ${Math.round((m.vol_h24 || 0) / liq)}x the pool size: money going round in circles.`, PEN.churn);
    if (m.boosted) chk("boosted", false, "Someone is paying DexScreener to promote it right now. Paid hype is a classic setup for selling to newcomers.", PEN.boosted);
    if (m.copycat) rej("copycat", "Another token with the same name or symbol has more money behind it. This one is likely a copy.");
    if (m.impersonator) rej("copycat", "Uses the name of a major coin or stablecoin but isn't the real one.");
    if (m.rules_changed) rej("rules_changed", "Its security settings changed in the last 3 days. Scammers change the rules after people buy.");
    const pen = Math.round(checks.reduce((s, c) => s + c.penalty, 0) * 1e6) / 1e6;
    return { verdict: hard.length ? "reject" : "pass", hard, checks, penalty: pen };
  }

  // ------------------------------------------------------------------ costs (dex/costs.py)
  // Sandwich bots only attack swaps big enough to pay their own two pool fees (see dex/costs.py)
  function mevShare(size, poolFee, depth) { const need = 2 * poolFee * depth; return need <= 0 ? 1 : Math.min(Math.max(size / need - 1, 0), 1); }
  function dexCost(amount, price, liqUsd, poolFee, buyTax, sellTax, gasUsd, mev, sniper) {
    const q = Math.max(liqUsd / 2, 1); const aIn = Math.max(amount - gasUsd, 0); const afterFee = aIn * (1 - poolFee);
    const imp = afterFee / (q + afterFee);
    const mb = mev * mevShare(afterFee, poolFee, q);
    const coins = afterFee * (1 - imp) / price * (1 - mb) * (1 - sniper) * (1 - buyTax);
    const buy = { gas: Math.min(gasUsd, amount), fee: aIn * poolFee, impact: afterFee * imp, mev: afterFee * (1 - imp) * mb,
      sniper: afterFee * (1 - imp) * (1 - mb) * sniper };
    buy.tax = afterFee * (1 - imp) * (1 - mb) * (1 - sniper) * buyTax;
    const at = (exitPx) => {
      const gross = coins * exitPx; const y = gross * (1 - sellTax); const z = y * (1 - poolFee); const simp = z / (q + z);
      const ms = mev * mevShare(z, poolFee, q);
      const recv = z * (1 - simp) * (1 - ms) - gasUsd;
      const sell = { tax: gross * sellTax, fee: y * poolFee, impact: z * simp, mev: z * (1 - simp) * ms, gas: gasUsd };
      const costs = {}; for (const k of ["fee", "impact", "tax", "mev", "gas"]) costs[k] = (buy[k] || 0) + (sell[k] || 0);
      costs.sniper = buy.sniper;
      return { net: recv - amount, received: recv, costs };
    };
    // break-even exit price by bisection (money out rises with the exit price)
    let lo = 0, hi = price, be = Infinity;
    if (coins > 0) {
      let ok = false;
      for (let i = 0; i < 200; i++) { if (at(hi).received >= amount) { ok = true; break; } hi *= 2; }
      if (ok) { for (let i = 0; i < 200; i++) { const mid = (lo + hi) / 2; if (at(mid).received >= amount) hi = mid; else lo = mid; } be = hi; }
    }
    const rt = amount > 0 ? -at(price).net / amount : 0;
    return { coins, impact_buy: imp, break_even: be, round_trip: rt, at };
  }

  const expectedR = (p, winR, lossR, costRt, ru, sl = 1) => p * winR - (1 - p) * lossR - costRt / (sl * ru);

  const api = { BASE, PEN, mevShare, thresholds, emptyFacts, fromGoplusEvm, fromGoplusSol, addHoneypotIs, addRugcheck, assess, dexCost, expectedR };
  root.OmegaDex = api;
  if (typeof module !== "undefined") module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
