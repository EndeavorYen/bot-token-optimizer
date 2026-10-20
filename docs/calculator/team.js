// Guided mode: turn plain-language answers into a team model, and turn fixes into a redesigned team.
// All costs come from calc.evaluateOrg (checked against the Python model).
import { evaluateOrg, priceRatios } from "./calc.js";

export const RATE = { quiet: 3, normal: 6, busy: 12 };            // hand-offs per link per day
export const AGE = { fresh: 12000, days: 40000, weeks: 90000, month: 150000 };   // context size
export const ACKS = { never: 0, sometimes: 0.5, usually: 1 };
export const GROUP = { none: 0, short: 80, long: 800 };
export const HANDOVER_AT = 60000;                                   // hand over when a context reaches this
const BASE = 8000, HANDOVER = 3000, ACK_TOKENS = 10, PING_TOKENS = 60;

export const DEFAULTS = { team: "pipeline", busy: "normal", age: "days", acks: "sometimes",
                          board: "yes", group: "short", relay: "no", model: "grok-4.7" };

// Work links only; acknowledgements are added later as replies to every work link.
const TEMPLATES = {
  pipeline: {
    hub: "chief",
    bots: [{ name: "chief", own: 2, rounds: 1 }, { name: "researcher", rounds: 3 },
           { name: "writer", rounds: 2 }, { name: "reviewer", rounds: 1 }],
    links: (r) => [["chief", "researcher", r, 800], ["researcher", "writer", r, 1500],
                   ["writer", "reviewer", r, 1500], ["reviewer", "chief", r, 600]],
  },
  manager: {
    hub: "chief",
    bots: [{ name: "chief", own: 3, rounds: 1 }, { name: "worker-1", rounds: 2 },
           { name: "worker-2", rounds: 2 }, { name: "worker-3", rounds: 2 }],
    links: (r) => ["worker-1", "worker-2", "worker-3"].flatMap((w) => [["chief", w, r, 800], [w, "chief", r, 1500]]),
  },
  support: {
    hub: "expert",
    bots: [{ name: "desk-1", ownPerRate: 4, rounds: 1 }, { name: "desk-2", ownPerRate: 4, rounds: 1 },
           { name: "expert", rounds: 2 }],
    links: (r) => ["desk-1", "desk-2"].flatMap((d) => [[d, "expert", r / 2, 1200], ["expert", d, r / 2, 800]]),
  },
};

export function teamNames(team) {
  return TEMPLATES[team].bots.map((b) => b.name);
}

/** Build an org description (same shape as org.json) from the answers. */
export function buildOrg(a) {
  const tpl = TEMPLATES[a.team];
  const r = RATE[a.busy];
  const ctx = AGE[a.age];
  let work = tpl.links(r).map(([from, to, n, tok]) => ({ from, to, messages_per_day: n, avg_tokens: tok }));
  const bots = tpl.bots.map((b) => ({
    name: b.name, context_tokens: b.name === tpl.hub ? Math.round(ctx * 1.5) : ctx, base_tokens: BASE,
    own_wakes_per_day: b.own ?? (b.ownPerRate ? b.ownPerRate * r : 0), work_rounds_per_wake: b.rounds,
  }));
  if (a.relay === "yes") {
    // a coordinator sits between the hub and everyone else and forwards every message
    bots.push({ name: "coordinator", context_tokens: ctx, base_tokens: BASE, own_wakes_per_day: 0, work_rounds_per_wake: 0 });
    work = work.flatMap((l) => (l.from === tpl.hub || l.to === tpl.hub)
      ? [{ ...l, to: "coordinator" }, { ...l, from: "coordinator" }]
      : [l]);
  }
  if (a.board === "yes") {
    // the content moves to a board: messages become short pings, and each receiver reads the
    // content from the board with one extra call (the same tokens end up in its context)
    const inbound = {};
    for (const l of work) (inbound[l.to] ||= []).push(l.avg_tokens);
    for (const b of bots) {
      const sizes = inbound[b.name];
      if (sizes) {
        b.reads_board_via_tool = true;
        b.board_read_tokens = Math.round(sizes.reduce((x, y) => x + y, 0) / sizes.length);
      }
    }
    work = work.map((l) => ({ ...l, avg_tokens: PING_TOKENS }));
  }
  const links = [...work];
  if (ACKS[a.acks] > 0) {
    for (const l of work) links.push({ from: l.to, to: l.from, messages_per_day: l.messages_per_day * ACKS[a.acks], avg_tokens: ACK_TOKENS });
  }
  const groups = GROUP[a.group] > 0
    ? [{ name: "team", members: bots.map((b) => b.name), messages_per_day: 2 * r, avg_tokens: GROUP[a.group] }]
    : [];
  return { horizon_days: 7, bots, links, groups };
}

/** Which fixes make sense for these answers. */
export function applicableFixes(a) {
  const out = [];
  if (a.acks !== "never") out.push("acks");
  if (a.board === "yes") out.push("board");
  if (a.relay === "yes") out.push("relay");
  if (a.group === "long") out.push("group");
  if (a.age !== "fresh") out.push("fresh");
  return out;
}

/** Answers after applying a set of fixes (all fixes except "fresh" are habits). */
function fixedAnswers(a, fixes) {
  const b = { ...a };
  if (fixes.has("acks")) b.acks = "never";
  if (fixes.has("board")) b.board = "no";
  if (fixes.has("relay")) b.relay = "no";
  if (fixes.has("group") && b.group === "long") b.group = "short";
  return b;
}

/** Evaluate the team with a set of fixes applied. Returns cost per week plus the team used. */
export function evaluate(a, fixes, pricing) {
  const org = buildOrg(fixedAnswers(a, fixes));
  let handoverEq = 0;
  if (fixes.has("fresh")) {
    // hand over every context when it reaches HANDOVER_AT: measure each bot's growth, then
    // start it fresh and keep it between a fresh context and the hand-over point
    const probe = evaluateOrg(org, pricing);
    const H = org.horizon_days;
    const { cache, out } = priceRatios(pricing);
    const cNew = BASE + HANDOVER;
    for (const b of org.bots) {
      const row = probe.bots.find((x) => x.bot === b.name);
      const g = (row.C_end - row.C_now) / H;
      const p = 0.8 * cache + 0.2;
      if (cNew + g * H <= HANDOVER_AT) {
        b.context_tokens = cNew;                                    // one hand-over at the start is enough
        b.growth_per_day = g;
        handoverEq += p * row.C_now + HANDOVER * out + cNew;
      } else {
        const resets = 1 + Math.max(0, (cNew + g * H - HANDOVER_AT) / (HANDOVER_AT - cNew));
        b.context_tokens = (cNew + HANDOVER_AT) / 2;                // average over a hand-over cycle
        b.growth_per_day = 0;
        handoverEq += resets * (p * HANDOVER_AT + HANDOVER * out + cNew);
      }
    }
  }
  const res = evaluateOrg(org, pricing);
  const totalEq = res.total_eq + handoverEq;
  return { org, res, handoverEq, totalEq, totalUsd: (totalEq * pricing.per_million.input) / 1e6 };
}

/** Current cost, cost with all chosen fixes, and what each fix saves on its own. */
export function analyze(a, chosen, pricing) {
  const fixes = applicableFixes(a);
  const now = evaluate(a, new Set(), pricing);
  const after = evaluate(a, new Set(fixes.filter((f) => chosen.has(f))), pricing);
  const alone = Object.fromEntries(fixes.map((f) => [f, now.totalUsd - evaluate(a, new Set([f]), pricing).totalUsd]));
  return { fixes, now, after, alone };
}
