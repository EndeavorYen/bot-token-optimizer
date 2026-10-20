// Checks that the web calculator (docs/calculator) computes exactly what the Python model computes.
// Run: node tests/calculator_parity.mjs
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import * as calc from "../docs/calculator/calc.js";
import { PRESETS } from "../docs/calculator/presets.js";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const py = JSON.parse(execFileSync("python3", [path.join(root, "tests", "_parity_cases.py")], { encoding: "utf8" }));
let failures = 0, checks = 0;

function same(a, b, where) {
  checks += 1;
  if (typeof a === "number" && typeof b === "number") {
    if (Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(a), Math.abs(b))) return;
  } else if (JSON.stringify(a) === JSON.stringify(b)) return;
  failures += 1;
  console.error(`MISMATCH ${where}: js=${JSON.stringify(a)} py=${JSON.stringify(b)}`);
}

function deep(js, ref, where) {
  if (ref && typeof ref === "object") {
    for (const key of Object.keys(ref)) deep(js?.[key], ref[key], `${where}.${key}`);
  } else same(js, ref, where);
}

// presets.js must mirror core/pricing/*.json
same(Object.keys(PRESETS).sort(), Object.keys(py.pricing).sort(), "preset names");
for (const [name, ref] of Object.entries(py.pricing)) {
  deep({ per_million: PRESETS[name]?.per_million, long_context_threshold: PRESETS[name]?.long_context_threshold,
         long_context_per_million: PRESETS[name]?.long_context_per_million }, ref, `preset ${name}`);
}

let i = 0;
for (let k = 0; k < 6; k++) for (const mode of ["sequential", "parallel"]) {
  deep(calc.boardOverhead(k, mode, 0.25), py.board[i++], `board k=${k} ${mode}`);
}
deep(calc.scenarioRelay(30000, 60000, 100000), py.relay, "relay");
deep(calc.scenarioAggregate(6, 30000, 60000, 100000), py.aggregate, "aggregate");
[[12, 30000, 1000, 6, true], [50, 30000, 200, 40, false], [7, 50000, 5000, 8, true]].forEach((a, j) => {
  deep(calc.optimalSpan(a[0], a[1], a[2], null, a[3], a[4]), py.span[j], `span ${j}`);
});
[5000, 1000, 200].forEach((m, j) => same(calc.optimalSpanClosed(30000, m), py.closed[j], `closed m=${m}`));
[[50000, 1, 0.8, "grok-4.7"], [150000, 2, 0.5, "claude-sonnet-5-5"], [300000, 1, 0.9, "gpt-6.1-sol"]].forEach((a, j) => {
  const r = calc.resetDecision(a[0], 8000, PRESETS[a[3]], { roundsPerWake: a[1], cacheHit: a[2] });
  deep(r, py.reset[j], `reset ${j}`);
});
const g = PRESETS["grok-4.7"];
deep(calc.growthCost(g, { c0: 20000, growth: 15000, wakesPerDay: 10, days: 14 }), py.growth[0], "growth no reset");
deep(calc.growthCost(g, { c0: 20000, growth: 15000, wakesPerDay: 10, days: 14, resetAt: 90000 }), py.growth[1], "growth reset");

console.log(`${checks - failures}/${checks} checks match the Python model`);
process.exit(failures ? 1 : 0);
