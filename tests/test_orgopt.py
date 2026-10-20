"""驗證 orgopt.py 與 analyze_usage.py。執行：python3 -m unittest discover -s tests -v"""
import json
import math
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
sys.path.insert(0, os.path.join(ROOT, "samples"))

import analyze_usage  # noqa: E402
import make_sample_usage  # noqa: E402
import orgopt  # noqa: E402

PRICING = orgopt.load_pricing()


class TestPricing(unittest.TestCase):
    def test_ratios(self):
        r_cache, r_out, long_mult = orgopt.price_ratios(PRICING)
        self.assertAlmostEqual(r_cache, 0.25)
        self.assertAlmostEqual(r_out, 3.0)
        self.assertAlmostEqual(long_mult, 2.0)

    def test_call_cost_and_long_context_tier(self):
        # 100k input（其中 80k 快取）+ 1k output：20k×2 + 80k×0.5 + 1k×6 = 86,000 / 1e6
        self.assertAlmostEqual(orgopt.call_cost_usd(100_000, 80_000, 1_000, PRICING), 0.086)
        # 超過 200K 全部改用長上下文價格（翻倍）
        self.assertAlmostEqual(orgopt.call_cost_usd(200_000, 0, 0, PRICING), 0.8)
        self.assertAlmostEqual(orgopt.call_cost_usd(199_999, 0, 0, PRICING), 199_999 * 2 / 1e6)


class TestPlatform(unittest.TestCase):
    def test_grok_bot_platform(self):
        plat = orgopt.load_platform(os.path.join(ROOT, "adapters", "grok-bot", "platform.json"))
        self.assertEqual(plat["span_cap"], 6)
        self.assertEqual(orgopt.load_pricing(plat["pricing_path"]), PRICING)

    def test_span_cap_from_platform(self):
        org = {"bots": [{"name": f"b{i}", "context_tokens": 1000} for i in range(8)],
               "links": [{"from": f"b{i}", "to": "b0", "messages_per_day": 1, "avg_tokens": 100}
                         for i in range(1, 8)]}
        generic = {(f["type"], f["bot"]) for f in orgopt.evaluate_org(org, PRICING)["findings"]}
        grok = {(f["type"], f["bot"]) for f in orgopt.evaluate_org(org, PRICING, span_cap=6)["findings"]}
        self.assertNotIn(("span", "b0"), generic)   # 7 個來源 ≤ 通用上限 8
        self.assertIn(("span", "b0"), grok)         # 超過 Grok Bot 的 6


class TestBoardOverhead(unittest.TestCase):
    """對照對話中的倍數表。"""
    def test_token_ratios(self):
        expect_seq = {0: 2.0, 1: 1.5, 2: 4 / 3, 4: 1.2}
        expect_par = {0: 1.5, 1: 1.25, 2: 7 / 6, 4: 1.1}
        for k, v in expect_seq.items():
            self.assertAlmostEqual(orgopt.board_overhead(k, "sequential")["token_ratio"], v)
        for k, v in expect_par.items():
            self.assertAlmostEqual(orgopt.board_overhead(k, "parallel")["token_ratio"], v)

    def test_cache_cost_ratios(self):
        cold = {0: 1.25, 2: 1.75 / 1.5, 4: 2.25 / 2.0}
        for k, v in cold.items():
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
        self.assertAlmostEqual(r["ratio"], 250 / 130)

    def test_aggregate(self):
        r = orgopt.scenario_aggregate(6, 30_000, 60_000, 100_000)
        self.assertEqual((r["flat"], r["hierarchy"]), (780_000, 700_000))
        self.assertLess(r["ratio"], 1)


class TestSpan(unittest.TestCase):
    def test_closed_form_table(self):
        self.assertAlmostEqual(orgopt.optimal_span_closed(30_000, 5_000), 1 + math.sqrt(13))
        self.assertAlmostEqual(orgopt.optimal_span_closed(30_000, 5_000), 4.606, places=3)
        self.assertAlmostEqual(orgopt.optimal_span_closed(30_000, 1_000), 8.810, places=3)
        self.assertAlmostEqual(orgopt.optimal_span_closed(30_000, 200), 18.349, places=3)

    def test_manager_cost(self):
        # 3 次喚醒：C0+m, C0+2m, C0+3m
        self.assertEqual(orgopt.manager_cost(3, 100, 10), 3 * 100 + 60)

    def test_tree_structure(self):
        r = orgopt.tree_cost(12, 6, 30_000, 1_000, assign_wake=False)
        self.assertEqual((r["managers"], r["depth"]), (3, 2))   # 2 個 leader + 1 個總管
        # 兩個帶 6 人的 leader + 總管帶 2 人
        expect = 2 * orgopt.manager_cost(6, 30_000, 1_000) + orgopt.manager_cost(2, 30_000, 1_000)
        self.assertEqual(r["cost"], expect)

    def test_even_split(self):
        # 7 人、k=6 → 分成 4+3，而不是 6+1（避免只帶 1 人的傳話主管）
        self.assertEqual(sorted(orgopt._even_sizes(7, 6)), [3, 4])

    def test_numeric_matches_closed_form_without_cap(self):
        # 不設上限且 N 很大時，數值最佳解應接近封閉解（忽略派工喚醒時）
        n, c0, m = 2000, 30_000, 1_000
        best = orgopt.optimal_span(n, c0, m, k_max=40, assign_wake=False)["best"]["k"]
        self.assertTrue(abs(best - orgopt.optimal_span_closed(c0, m)) <= 2, best)

    def test_shorter_reports_allow_wider_span(self):
        wide = orgopt.optimal_span(400, 30_000, 200, k_max=40)["best"]["k"]
        narrow = orgopt.optimal_span(400, 30_000, 5_000, k_max=40)["best"]["k"]
        self.assertGreater(wide, narrow)

    def test_cap_respected(self):
        r = orgopt.optimal_span(50, 30_000, 200, k_max=6)
        self.assertLessEqual(r["best"]["k"], 6)


class TestReset(unittest.TestCase):
    def test_hand_calculation(self):
        # C=150k, C_base=10k, H=3k, 快取命中 0.8 → p = 0.8×0.25 + 0.2 = 0.4
        r = orgopt.reset_decision(150_000, 10_000, 3_000, wakes_per_day=10, horizon_days=7,
                                  pricing=PRICING)
        self.assertEqual(r["c_new"], 13_000)
        self.assertAlmostEqual(r["rebuild_cost_eq"], 0.4 * 150_000 + 3_000 * 3 + 13_000)   # 82,000
        self.assertAlmostEqual(r["saving_per_wake_eq"], 0.4 * 137_000)                     # 54,800
        self.assertAlmostEqual(r["breakeven_wakes"], 82_000 / 54_800)
        self.assertTrue(r["recommend"])

    def test_small_context_not_recommended(self):
        r = orgopt.reset_decision(20_000, 10_000, 3_000, wakes_per_day=10, pricing=PRICING)
        self.assertFalse(r["recommend"])

    def test_long_context_forces_reset(self):
        r = orgopt.reset_decision(210_000, 10_000, wakes_per_day=0, pricing=PRICING)
        self.assertTrue(r["recommend"])


class TestOrg(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "samples", "org_before.json"), encoding="utf-8") as f:
            self.before = json.load(f)
        with open(os.path.join(ROOT, "samples", "org_after.json"), encoding="utf-8") as f:
            self.after = json.load(f)

    def test_detects_planted_patterns(self):
        res = orgopt.evaluate_org(self.before, PRICING)
        kinds = {(f["type"], f["bot"]) for f in res["findings"]}
        self.assertIn(("relay", "lead"), kinds)
        self.assertIn(("ack", "researcher"), kinds)
        self.assertIn(("ack", "writer"), kinds)
        self.assertIn(("board_tool_read", "researcher"), kinds)
        self.assertIn(("group_heavy", "all-hands"), kinds)
        self.assertIn(("reset", "chief"), kinds)

    def test_after_is_clean_and_cheaper(self):
        res = orgopt.compare_orgs(self.before, self.after, PRICING)
        self.assertEqual(res["after"]["findings"], [])
        self.assertLess(res["ratio"], 0.5)

    def test_wake_accounting(self):
        org = {"horizon_days": 1, "bots": [
            {"name": "a", "context_tokens": 10_000, "own_wakes_per_day": 2, "work_rounds_per_wake": 1,
             "output_tokens_per_wake": 0, "cache_hit_ratio": 0},
            {"name": "b", "context_tokens": 10_000, "work_rounds_per_wake": 0,
             "output_tokens_per_wake": 0, "cache_hit_ratio": 0, "reads_board_via_tool": True,
             "board_read_tokens": 0}],
            "links": [{"from": "a", "to": "b", "messages_per_day": 4, "avg_tokens": 0}]}
        res = orgopt.evaluate_org(org, PRICING)
        rows = {r["bot"]: r for r in res["bots"]}
        self.assertEqual(rows["a"]["rounds_per_day"], 4)      # 2 次喚醒 × 2 輪
        self.assertEqual(rows["b"]["rounds_per_day"], 8)      # 4 次喚醒 × 1 輪 + 讀 board 4 輪
        # 無成長、無快取：成本 = 輪數 × C
        self.assertAlmostEqual(rows["b"]["cost_eq"], 8 * 10_000)

    def test_growth_override(self):
        org = {"horizon_days": 2, "bots": [{"name": "a", "context_tokens": 10_000, "own_wakes_per_day": 1,
                                            "work_rounds_per_wake": 0, "cache_hit_ratio": 0,
                                            "growth_per_day": 5_000}]}
        row = orgopt.evaluate_org(org, PRICING)["bots"][0]
        self.assertAlmostEqual(row["C_end"], 20_000)
        # 平均 C = 15,000，2 天各 1 輪
        self.assertAlmostEqual(row["cost_eq"], 2 * 15_000)

    def test_unknown_bot_rejected(self):
        org = {"bots": [{"name": "a", "context_tokens": 1}],
               "links": [{"from": "a", "to": "ghost", "messages_per_day": 1, "avg_tokens": 1}]}
        with self.assertRaises(ValueError):
            orgopt.evaluate_org(org, PRICING)


class TestAnalyzeUsage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.csv = os.path.join(cls.tmp, "u.csv")
        make_sample_usage.generate(cls.csv)
        rows, cls.mapping, _ = analyze_usage.load_rows(cls.csv)
        cls.res = analyze_usage.analyze(rows, PRICING, soft_cap=120_000)

    def test_column_mapping(self):
        self.assertEqual(self.mapping["bot"], "bot_name")
        self.assertEqual(self.mapping["cached"], "cached_input_tokens")

    def test_detects_planted_patterns(self):
        kinds = {(f["type"], f["bot"]) for f in self.res["findings"]}
        self.assertIn(("ack", "writer"), kinds)
        self.assertIn(("read_first", "researcher"), kinds)
        self.assertIn(("growth", "chief"), kinds)
        self.assertIn(("cache", "reviewer"), kinds)

    def test_measured_parameters(self):
        b = {x["bot"]: x for x in self.res["bots"]}
        self.assertAlmostEqual(b["chief"]["C_growth_per_day"], 15_000, delta=1_500)
        self.assertAlmostEqual(b["researcher"]["rounds_per_wake"], 3.0, places=5)
        self.assertAlmostEqual(b["reviewer"]["cache_hit"], 0.30, places=2)
        self.assertAlmostEqual(b["chief"]["wakes_per_day"], 6, delta=0.5)

    def test_calibration(self):
        self.assertLess(abs(self.res["calibration"]["error"]), 0.15)

    def test_map_override_and_missing(self):
        path = os.path.join(self.tmp, "custom.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("日期,機器人,輸入\n2026-10-01 10:00:00,a,1000\n2026-10-01 11:00:00,a,1200\n")
        with self.assertRaises(ValueError):
            analyze_usage.load_rows(path)
        rows, mapping, _ = analyze_usage.load_rows(path, {"time": "日期", "bot": "機器人", "input": "輸入"})
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["bot"], "a")

    def test_cached_separate(self):
        path = os.path.join(self.tmp, "sep.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("timestamp,bot,input_tokens,cached_tokens\n2026-10-01T00:00:00Z,a,200,800\n")
        rows, _, _ = analyze_usage.load_rows(path, cached_separate=True)
        self.assertEqual((rows[0]["input"], rows[0]["cached"]), (1000, 800))

    def test_epoch_ms(self):
        self.assertEqual(analyze_usage.parse_time("1759312800000").year, 2025)

    def test_skeleton_runs_in_model(self):
        org = analyze_usage.org_skeleton(self.res)
        out = orgopt.evaluate_org(org, PRICING)
        self.assertEqual(len(out["bots"]), 4)


if __name__ == "__main__":
    unittest.main()
