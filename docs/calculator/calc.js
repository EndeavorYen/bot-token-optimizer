// Cost formulas for the web calculator. A line-by-line port of core/orgopt.py;
// tests/calculator_parity.mjs checks every function against the Python model.

export function priceRatios(p) {
  const pm = p.per_million;
  const lp = p.long_context_per_million || pm;
  return { cache: pm.cached_input / pm.input, out: pm.output / pm.input, longIn: lp.input / pm.input };
}

export function boardOverhead(k, mode = "sequential", cacheRate = 0.25) {
  if (k < 0) throw new Error("k must be >= 0");
  const tokenRatio = mode === "sequential" ? (k + 2) / (k + 1) : 1 + 1 / (2 * (k + 1));
  const r = cacheRate;
  return {
    k, mode, token_ratio: tokenRatio,
    receiver_cost_ratio_cold_cache: (1 + r * (k + 1)) / (1 + r * k),
    receiver_cost_ratio_warm_cache: (k + 2) / (k + 1),
  };
}

export function scenarioRelay(cw, cl, cc) {
  const flat = cw + cc, hierarchy = cw + 2 * cl + cc;
  return { flat, hierarchy, ratio: hierarchy / flat };
}

export function scenarioAggregate(n, cw, cl, cc) {
  const flat = n * cw + n * cc, hierarchy = n * cw + (n + 1) * cl + cc;
  return { flat, hierarchy, ratio: hierarchy / flat };
}

export function managerCost(k, c0, m) {
  return k * c0 + (m * k * (k + 1)) / 2;
}

export function optimalSpanClosed(c0, m) {
  return m <= 0 ? Infinity : 1 + Math.sqrt(1 + (2 * c0) / m);
}

function evenSizes(nodes, k) {
  const groups = Math.ceil(nodes / k);
  const base = Math.floor(nodes / groups), extra = nodes % groups;
  return [...Array(extra).fill(base + 1), ...Array(groups - extra).fill(base)];
}

export function treeCost(n, k, c0, m, c0Chief = null, assignWake = true) {
  if (n < 2 || k < 2) throw new Error("n and k must both be >= 2");
  const levels = [];
  let nodes = n;
  for (;;) {
    const sizes = evenSizes(nodes, k);
    levels.push(sizes);
    if (sizes.length === 1) break;
    nodes = sizes.length;
  }
  let cost = 0, managers = 0;
  levels.forEach((sizes, li) => {
    const isRoot = li === levels.length - 1;
    for (const s of sizes) {
      const c = isRoot && c0Chief !== null ? c0Chief : c0;
      cost += managerCost(s, c, m);
      if (assignWake && !isRoot) cost += c;
      managers += 1;
    }
  });
  return { k, cost, managers, depth: levels.length };
}

export function optimalSpan(n, c0, m, c0Chief = null, kMax = 8, assignWake = true) {
  const upper = Math.max(2, Math.min(n, kMax || n));
  const table = [];
  for (let k = 2; k <= upper; k++) table.push(treeCost(n, k, c0, m, c0Chief, assignWake));
  const best = table.reduce((a, b) => (b.cost < a.cost || (b.cost === a.cost && b.depth < a.depth) ? b : a));
  return { best, table, closed_form_k: optimalSpanClosed(c0, m), k_cap: kMax };
}

export function resetDecision(c, cBase, pricing, { handover = 3000, wakesPerDay = 10, horizonDays = 7,
  roundsPerWake = 1, cacheHit = 0.8 } = {}) {
  const { cache, out } = priceRatios(pricing);
  const p = cacheHit * cache + (1 - cacheHit);
  const cNew = cBase + handover;
  const rebuild = p * c + handover * out + cNew;
  const savingPerWake = roundsPerWake * p * (c - cNew);
  const breakeven = savingPerWake > 0 ? rebuild / savingPerWake : Infinity;
  const expected = wakesPerDay * horizonDays;
  const net = expected * savingPerWake - rebuild;
  return {
    c, c_new: cNew, rebuild_cost_eq: rebuild, saving_per_wake_eq: savingPerWake,
    breakeven_wakes: breakeven, expected_wakes: expected, net_saving_eq: net,
    net_saving_usd: (net * pricing.per_million.input) / 1e6,
  };
}

// Daily cost of one bot whose context grows linearly (for the "context growth" card).
export function growthCost(pricing, { c0, growth, wakesPerDay, days, resetAt = null, cNew = 11000, cacheHit = 0.8, dt = 0.01 }) {
  const thr = pricing.long_context_threshold || Infinity;
  const pm = pricing.per_million, lp = pricing.long_context_per_million || pm;
  const callCost = (inp, cached, outTok) => {
    const tb = inp >= thr ? lp : pm;
    return ((inp - cached) * tb.input + cached * tb.cached_input + outTok * tb.output) / 1e6;
  };
  let cost = 0, c = c0, resets = 0;
  for (let t = 0; t <= days + 1e-9; t += dt) {
    cost += wakesPerDay * dt * callCost(c, cacheHit * c, 0);
    c += growth * dt;
    if (resetAt && c >= resetAt) {
      cost += callCost(c, cacheHit * c, 3000);
      c = cNew;
      resets += 1;
    }
  }
  return { cost, resets };
}

export function longMultipliers(p) {
  const pm = p.per_million;
  const lp = p.long_context_per_million || pm;
  return { input: lp.input / pm.input, output: lp.output / pm.output };
}

// Port of orgopt.evaluate_org (cost part): input + output cost per bot over the horizon.
export function evaluateOrg(org, pricing) {
  const { cache: rCache, out: rOut } = priceRatios(pricing);
  const lm = longMultipliers(pricing);
  const thr = pricing.long_context_threshold || Infinity;
  const H = org.horizon_days ?? 7;
  const bots = new Map();
  for (const b of org.bots) {
    const c = b.context_tokens;
    const reads = Boolean(b.reads_board_via_tool);
    bots.set(b.name, {
      name: b.name, C: c, base: b.base_tokens ?? Math.min(c, 8000), own: b.own_wakes_per_day ?? 0,
      k: b.work_rounds_per_wake ?? 1, readsBoard: reads, boardRead: b.board_read_tokens ?? (reads ? 2000 : 0),
      out: (b.output_tokens_per_wake ?? 300) + (b.reasoning_tokens_per_wake ?? 0),
      cacheHit: b.cache_hit_ratio ?? 0.8, growthOverride: b.growth_per_day ?? null, msgWakes: 0, growth: 0,
    });
  }
  for (const ln of org.links || []) {
    const to = bots.get(ln.to);
    if (!to || !bots.has(ln.from)) throw new Error(`unknown bot in link ${ln.from} -> ${ln.to}`);
    to.msgWakes += ln.messages_per_day;
    to.growth += ln.messages_per_day * ln.avg_tokens;
  }
  for (const g of org.groups || []) {
    const wakeAll = (g.wake || "addressed") === "all";
    for (const m of g.members) {
      const b = bots.get(m);
      b.growth += g.messages_per_day * g.avg_tokens;
      b.msgWakes += wakeAll ? g.messages_per_day : g.messages_per_day / g.members.length;
    }
  }
  let total = 0, totalOut = 0;
  const rows = [];
  for (const b of bots.values()) {
    const wakes = b.own + b.msgWakes;
    const extraBoard = b.readsBoard ? b.msgWakes : 0;
    const rounds = wakes * (1 + b.k) + extraBoard;
    let growth = b.growth + wakes * b.out + extraBoard * b.boardRead;
    if (b.growthOverride !== null) growth = b.growthOverride;
    const p = b.cacheHit * rCache + (1 - b.cacheHit);
    let cin = 0, cout = 0;
    const nSteps = Math.max(1, Math.trunc(H * 24));
    const dt = H / nSteps;
    for (let i = 0; i < nSteps; i++) {
      const ct = b.C + growth * (i + 0.5) * dt;
      const long = ct >= thr;
      cin += rounds * dt * ct * p * (long ? lm.input : 1);
      cout += wakes * dt * b.out * rOut * (long ? lm.output : 1);
    }
    total += cin + cout;
    totalOut += cout;
    rows.push({ bot: b.name, C_now: b.C, C_end: b.C + growth * H, wakes_per_day: wakes, rounds_per_day: rounds,
                input_eq: cin, output_eq: cout, cost_eq: cin + cout,
                cost_usd: ((cin + cout) * pricing.per_million.input) / 1e6 });
  }
  rows.sort((a, b) => b.cost_eq - a.cost_eq);
  const usd = (x) => (x * pricing.per_million.input) / 1e6;
  return { horizon_days: H, total_eq: total, total_usd: usd(total), output_usd: usd(totalOut), bots: rows };
}
