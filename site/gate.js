/* The gate (DECISIONS D115-D117): which ideas may be put in front of people.

   Reads three small files the summary job writes every 15 minutes:
     proof.json   which parts of the product are Proven (passed the honest tests) and which are Testing only
     market.json  whether the market filter is on and the crypto market is falling
     best.json    the home page's "Best idea right now"
   The pages grey out Testing-only ideas, and the alerts only ever fire for ideas this gate allows. If the files can't
   be read, nothing is allowed (the gate fails closed: no alert is better than an unproven one). */
(function (root) {
  "use strict";
  const REPO = root.OMEGA_REPO || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/main/state/";
  const CRYPTO = new Set(["main", "small", "dex", "sniper", "listings", "whales", "accum", "hayes", "deep"]);
  const g = { proof: null, market: null, best: null, loaded: false };
  async function get(name) {
    try {
      const r = await fetch(`${REPO}${name}?t=${Math.floor(Date.now() / 300000)}`);
      return r.ok ? await r.json() : null;
    } catch { return null; }
  }
  async function load() {
    const [p, m, b] = await Promise.all([get("proof.json"), get("market.json"), get("best.json")]);
    Object.assign(g, { proof: p, market: m, best: b, loaded: true });
    return g;
  }
  g.ready = load();
  g.reload = () => (g.ready = load());
  g.section = (key) => (g.proof && g.proof.sections && g.proof.sections[key]) || null;
  g.unit = (id) => ((g.proof && g.proof.units) || []).find((u) => u.id === id) || null;
  // is this idea from a proven part of the product? (a unit id like "lab:rsi2", or just the section)
  g.proven = (section, unit) => {
    if (!g.proof) return false;
    if (unit) return (g.proof.proven_ids || []).includes(unit);
    const s = g.section(section);
    return !!(s && s.status === "proven");
  };
  g.isCrypto = (section, unit) => (section === "lab" || section === "ict" ? !!(unit && /USDT|crypto/i.test(unit)) : CRYPTO.has(section));
  g.marketPaused = () => !!(g.market && g.market.active);
  // may this idea be promoted (alerts, the home card)? proven, and not a crypto buy while the market filter pauses them
  g.allows = (section, unit, side, crypto) => g.proven(section, unit) &&
    !(g.marketPaused() && (crypto ?? g.isCrypto(section, unit)) && String(side || "buy").toLowerCase() !== "sell" && !(+side < 0));
  root.OMEGA_GATE = g;
})(window);
