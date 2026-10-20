"""Cost model: hand-calculated values from the guide, organization evaluation, presets."""
import json
import math
import os
import unittest

from _paths import ROOT
import orgopt

PRICING = orgopt.load_pricing("grok-4.7")


class TestPricing(unittest.TestCase):
    def test_ratios(self):
        r_cache, r_out, long_mult = orgopt.price_ratios(PRICING)
        self.assertAlmostEqual(r_cache, 0.25)
        self.assertAlmostEqual(r_out, 3.0)
        self.assertAlmostEqual(long_mult, 2.0)
        self.assertEqual(orgopt.long_multipliers(PRICING), (2.0, 2.0))

    def test_call_cost_and_long_context_tier(self):
        # 100k input (80k cached) + 1k output: 20k×2 + 80k×0.5 + 1k×6 = 86,000 / 1e6
        self.assertAlmostEqual(orgopt.call_cost_usd(100_000, 80_000, 1_000, PRICING), 0.086)
        self.assertAlmostEqual(orgopt.call_cost_usd(200_000, 0, 0, PRICING), 0.8)
        self.assertAlmostEqual(orgopt.call_cost_usd(199_999, 0, 0, PRICING), 199_999 * 2 / 1e6)

    def test_all_presets_are_well_formed(self):
        for name in orgopt.list_presets("pricing"):
            p = orgopt.load_pricing(name)
            pm = p["per_million"]
            self.assertGreater(pm["input"], 0, name)
            self.assertLessEqual(pm["cached_input"], pm["input"], name)
            self.assertGreater(pm["output"], 0, name)
            if p.get("long_context_threshold"):
                lp = p["long_context_per_million"]
                self.assertGreaterEqual(lp["input"], pm["input"], name)
            for key in ("vendor", "model", "source", "checked"):
                self.assertIn(key, p, name)

    def test_openai_long_output_multiplier_differs(self):
        p = orgopt.load_pricing("gpt-6.1-sol")
        self.assertEqual(orgopt.long_multipliers(p), (2.0, 1.5))

    def test_platform_presets(self):
        grok = orgopt.load_platform("grok-bot")
        self.assertEqual(grok["span_cap"], 6)
        self.assertEqual(orgopt.load_pricing(grok["pricing_path"]), PRICING)
        self.assertIn("generic", orgopt.list_presets("platforms"))
        with self.assertRaises(FileNotFoundError):
            orgopt.load_platform("no-such-platform")


class TestBoardOverhead(unittest.TestCase):
    def test_token_ratios(self):
        for k, v in {0: 2.0, 1: 1.5, 2: 4 / 3, 4: 1.2}.items():
            self.assertAlmostEqual(orgopt.board_overhead(k, "sequential")["token_ratio"], v)
        for k, v in {0: 1.5, 1: 1.25, 2: 7 / 6, 4: 1.1}.items():
            self.assertAlmostEqual(orgopt.board_overhead(k, "parallel")["token_ratio"], v)

    def test_cache_cost_ratios(self):
        for k, v in {0: 1.25, 2: 1.75 / 1.5, 4: 2.25 / 2.0}.items():
            r = orgopt.board_overhead(k, "sequential", 0.25)
            self.assertAlmostEqual(r["receiver_cost_ratio_cold_cache"], v)
            self.assertAlmostEqual(r["receiver_cost_ratio_warm_cache"], (k + 2) / (k + 1))

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            orgopt.board_overhead(-1)
        with self.assertRaises(ValueError):
            orgopt.board_overhead(1, "bogus")


class TestScenarios(unittest.TestCase):
    def test_relay(self):
        r = orgopt.scenario_relay(30_000, 60_000, 100_000)
        self.assertEqual((r["flat"], r["hierarchy"]), (130_000, 250_000))

    def test_aggregate(self):
        r = orgopt.scenario_aggregate(6, 30_000, 60_000, 100_000)
        self.assertEqual((r["flat"], r["hierarchy"]), (780_000, 700_000))


class TestSpan(unittest.TestCase):
    def test_closed_form_table(self):
        self.assertAlmostEqual(orgopt.optimal_span_closed(30_000, 5_000), 1 + math.sqrt(13))
        self.assertAlmostEqual(orgopt.optimal_span_closed(30_000, 1_000), 8.810, places=3)
        self.assertAlmostEqual(orgopt.optimal_span_closed(30_000, 200), 18.349, places=3)

    def test_manager_cost(self):
        self.assertEqual(orgopt.manager_cost(3, 100, 10), 3 * 100 + 60)

    def test_tree_structure(self):
        r = orgopt.tree_cost(12, 6, 30_000, 1_000, assign_wake=False)
        self.assertEqual((r["managers"], r["depth"]), (3, 2))
        expect = 2 * orgopt.manager_cost(6, 30_000, 1_000) + orgopt.manager_cost(2, 30_000, 1_000)
        self.assertEqual(r["cost"], expect)

    def test_even_split(self):
        self.assertEqual(sorted(orgopt._even_sizes(7, 6)), [3, 4])

    def test_numeric_matches_closed_form(self):
        for m in (5_000, 1_000, 200):
            best = orgopt.optimal_span(2000, 30_000, m, k_max=40, assign_wake=False)["best"]["k"]
            self.assertLessEqual(abs(best - orgopt.optimal_span_closed(30_000, m)), 1.0, m)

    def test_cap_respected(self):
        self.assertLessEqual(orgopt.optimal_span(50, 30_000, 200, k_max=6)["best"]["k"], 6)


class TestReset(unittest.TestCase):
    def test_hand_calculation(self):
        # p = 0.8×0.25 + 0.2 = 0.4
        r = orgopt.reset_decision(150_000, 10_000, 3_000, wakes_per_day=10, horizon_days=7, pricing=PRICING)
        self.assertEqual(r["c_new"], 13_000)
        self.assertAlmostEqual(r["rebuild_cost_eq"], 0.4 * 150_000 + 3_000 * 3 + 13_000)
        self.assertAlmostEqual(r["saving_per_wake_eq"], 0.4 * 137_000)
        self.assertAlmostEqual(r["breakeven_wakes"], 82_000 / 54_800)
        self.assertTrue(r["recommend"])

    def test_guide_table(self):
        expect = {50_000: (2.56, 2.10), 100_000: (1.69, 4.86), 150_000: (1.44, 7.62)}
        for c, (be, usd) in expect.items():
            r = orgopt.reset_decision(c, 8_000, 3_000, wakes_per_day=10, horizon_days=7, pricing=PRICING)
            self.assertAlmostEqual(r["breakeven_wakes"], be, places=2)
            self.assertAlmostEqual(r["net_saving_usd"], usd, places=2)

    def test_small_context_not_recommended(self):
        self.assertFalse(orgopt.reset_decision(20_000, 10_000, 3_000, pricing=PRICING)["recommend"])

    def test_long_context_forces_reset_in_both_languages(self):
        en = orgopt.reset_decision(210_000, 10_000, wakes_per_day=0, pricing=PRICING, lang="en")
        zh = orgopt.reset_decision(210_000, 10_000, wakes_per_day=0, pricing=PRICING, lang="zh-TW")
        self.assertTrue(en["recommend"] and zh["recommend"])
        self.assertIn("long-context", en["reasons"][0])
        self.assertIn("長上下文", zh["reasons"][0])


class TestOrg(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "samples", "org_before.json"), encoding="utf-8") as f:
            self.before = json.load(f)
        with open(os.path.join(ROOT, "samples", "org_after.json"), encoding="utf-8") as f:
            self.after = json.load(f)

    def test_detects_planted_patterns(self):
        kinds = {(f["type"], f["bot"]) for f in orgopt.evaluate_org(self.before, PRICING)["findings"]}
        for expected in [("relay", "lead"), ("ack", "researcher"), ("ack", "writer"),
                         ("board_tool_read", "researcher"), ("group_heavy", "all-hands"),
                         ("reset", "chief"), ("latency", "chief")]:
            self.assertIn(expected, kinds)

    def test_after_is_clean_and_cheaper(self):
        res = orgopt.compare_orgs(self.before, self.after, PRICING)
        self.assertEqual(res["after"]["findings"], [])
        self.assertLess(res["ratio"], 0.5)

    def test_wake_accounting(self):
        org = {"horizon_days": 1, "bots": [
            {"name": "a", "context_tokens": 10_000, "own_wakes_per_day": 2, "work_rounds_per_wake": 1,
             "output_tokens_per_wake": 0, "cache_hit_ratio": 0},
            {"name": "b", "context_tokens": 10_000, "work_rounds_per_wake": 0, "output_tokens_per_wake": 0,
             "cache_hit_ratio": 0, "reads_board_via_tool": True, "board_read_tokens": 0}],
            "links": [{"from": "a", "to": "b", "messages_per_day": 4, "avg_tokens": 0}]}
        rows = {r["bot"]: r for r in orgopt.evaluate_org(org, PRICING)["bots"]}
        self.assertEqual(rows["a"]["rounds_per_day"], 4)
        self.assertEqual(rows["b"]["rounds_per_day"], 8)
        self.assertAlmostEqual(rows["b"]["cost_eq"], 8 * 10_000)

    def test_output_and_reasoning_are_priced(self):
        org = {"horizon_days": 1, "bots": [{"name": "a", "context_tokens": 0, "own_wakes_per_day": 10,
                                            "work_rounds_per_wake": 0, "output_tokens_per_wake": 100,
                                            "reasoning_tokens_per_wake": 400, "growth_per_day": 0}]}
        res = orgopt.evaluate_org(org, PRICING)
        # 10 wake-ups × 500 output tokens × 3 (output/input price) = 15,000 input-token equivalents
        self.assertAlmostEqual(res["bots"][0]["output_eq"], 15_000)
        self.assertAlmostEqual(res["output_usd"], 15_000 * 2 / 1e6)

    def test_growth_override(self):
        org = {"horizon_days": 2, "bots": [{"name": "a", "context_tokens": 10_000, "own_wakes_per_day": 1,
                                            "work_rounds_per_wake": 0, "cache_hit_ratio": 0,
                                            "output_tokens_per_wake": 0, "growth_per_day": 5_000}]}
        row = orgopt.evaluate_org(org, PRICING)["bots"][0]
        self.assertAlmostEqual(row["C_end"], 20_000)
        self.assertAlmostEqual(row["cost_eq"], 2 * 15_000)

    def test_span_cap_from_platform(self):
        org = {"bots": [{"name": f"b{i}", "context_tokens": 1000} for i in range(8)],
               "links": [{"from": f"b{i}", "to": "b0", "messages_per_day": 1, "avg_tokens": 100} for i in range(1, 8)]}
        generic = {(f["type"], f["bot"]) for f in orgopt.evaluate_org(org, PRICING)["findings"]}
        grok = {(f["type"], f["bot"]) for f in orgopt.evaluate_org(org, PRICING, span_cap=6)["findings"]}
        self.assertNotIn(("span", "b0"), generic)
        self.assertIn(("span", "b0"), grok)

    def test_longest_chain(self):
        links = [{"from": a, "to": b} for a, b in [("a", "b"), ("b", "c"), ("c", "d"), ("a", "d")]]
        self.assertEqual(orgopt.longest_chain(links), {"hops": 3, "path": ["a", "b", "c", "d"]})
        loop = [{"from": a, "to": b} for a, b in [("a", "b"), ("b", "a")]]
        self.assertEqual(orgopt.longest_chain(loop)["hops"], 2)
        self.assertIsNone(orgopt.longest_chain([]))

    def test_reports_in_both_languages(self):
        res_en = orgopt.evaluate_org(self.before, PRICING, lang="en")
        res_zh = orgopt.evaluate_org(self.before, PRICING, lang="zh-TW")
        self.assertIn("Diagnosis", orgopt.org_report_md(res_en, "en"))
        self.assertIn("診斷與建議", orgopt.org_report_md(res_zh, "zh-TW"))
        self.assertEqual([f["type"] for f in res_en["findings"]], [f["type"] for f in res_zh["findings"]])


if __name__ == "__main__":
    unittest.main()
