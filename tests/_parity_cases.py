"""Reference values from the Python model for tests/calculator_parity.mjs (prints JSON)."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
sys.path.insert(0, os.path.join(ROOT, "docs", "images"))
import orgopt  # noqa: E402
import make_figures  # noqa: E402

pricing = {name: orgopt.load_pricing(name) for name in orgopt.list_presets("pricing")}
grok = pricing["grok-4.7"]
out = {
    "pricing": {k: {"per_million": v["per_million"], "long_context_threshold": v.get("long_context_threshold"),
                    "long_context_per_million": v.get("long_context_per_million")} for k, v in pricing.items()},
    "board": [orgopt.board_overhead(k, mode, 0.25) for k in range(6) for mode in ("sequential", "parallel")],
    "relay": orgopt.scenario_relay(30000, 60000, 100000),
    "aggregate": orgopt.scenario_aggregate(6, 30000, 60000, 100000),
    "span": [orgopt.optimal_span(n, c0, m, None, cap, aw)
             for n, c0, m, cap, aw in [(12, 30000, 1000, 6, True), (50, 30000, 200, 40, False), (7, 50000, 5000, 8, True)]],
    "closed": [orgopt.optimal_span_closed(30000, m) for m in (5000, 1000, 200)],
    "reset": [orgopt.reset_decision(c, 8000, 3000, 10, 7, r, h, pricing[p])
              for c, r, h, p in [(50000, 1, 0.8, "grok-4.7"), (150000, 2, 0.5, "claude-sonnet-5-5"), (300000, 1, 0.9, "gpt-6.1-sol")]],
    "growth": [{"cost": make_figures.growth_series()[1], "resets": make_figures.growth_series()[2]},
               {"cost": make_figures.growth_series(reset_at=90000)[1], "resets": make_figures.growth_series(reset_at=90000)[2]}],
}
for r in out["board"]:
    r.pop("extra_tokens_per_notification", None)
for r in out["reset"]:
    r.pop("reasons", None)
    r.pop("recommend", None)
print(json.dumps(out))
