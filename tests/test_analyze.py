"""Usage CSV analyzer: column mapping, per-chat contexts, hand-over detection, calibration."""
import os
import tempfile
import unittest

from _paths import ROOT  # noqa: F401
import analyze_usage
import make_sample_usage
import orgopt

PRICING = orgopt.load_pricing("grok-4.7")


class TestAnalyzeSample(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.csv = os.path.join(cls.tmp, "u.csv")
        make_sample_usage.generate(cls.csv)
        rows, cls.mapping, _ = analyze_usage.load_rows(cls.csv)
        cls.res = analyze_usage.analyze(rows, PRICING, soft_cap=120_000)
        cls.u = {u["unit"]: u for u in cls.res["units"]}

    def test_column_mapping(self):
        self.assertEqual(self.mapping["bot"], "bot_name")
        self.assertEqual(self.mapping["chat"], "chat_id")
        self.assertEqual(self.mapping["cached"], "cached_input_tokens")

    def test_each_chat_is_its_own_context(self):
        self.assertIn("support/a", self.u)
        self.assertIn("support/b", self.u)
        self.assertAlmostEqual(self.u["support/a"]["C_growth_per_day"], 6_000, delta=600)
        self.assertAlmostEqual(self.u["support/b"]["C_growth_per_day"], 1_000, delta=300)
        self.assertIn("writer", self.u)          # single-chat bots keep the plain name

    def test_handover_detected_and_growth_not_distorted(self):
        w = self.u["writer"]
        self.assertEqual(w["resets_seen"], 1)
        self.assertAlmostEqual(w["C_growth_per_day"], 5_000, delta=800)
        self.assertLess(w["C_now"], 40_000)     # measured after the hand-over

    def test_detects_planted_patterns(self):
        kinds = {(f["type"], f["unit"]) for f in self.res["findings"]}
        for expected in [("ack", "writer"), ("read_first", "researcher"), ("growth", "chief"), ("cache", "reviewer")]:
            self.assertIn(expected, kinds)

    def test_measured_parameters(self):
        self.assertAlmostEqual(self.u["chief"]["C_growth_per_day"], 15_000, delta=1_500)
        self.assertAlmostEqual(self.u["researcher"]["rounds_per_wake"], 3.0, places=5)
        self.assertAlmostEqual(self.u["reviewer"]["cache_hit"], 0.30, places=2)

    def test_calibration_includes_output(self):
        c = self.res["calibration"]
        self.assertLess(abs(c["error"]), 0.15)
        self.assertAlmostEqual(c["actual_usd"], self.res["total_usd"])

    def test_skeleton_validates_and_runs(self):
        import orgspec
        org = analyze_usage.org_skeleton(self.res)
        orgspec.check_org(org)
        self.assertEqual(len(orgopt.evaluate_org(org, PRICING)["bots"]), len(self.res["units"]))

    def test_report_languages(self):
        self.assertIn("Findings", analyze_usage.report_md(self.res, "en"))
        rows, _, _ = analyze_usage.load_rows(self.csv)
        zh = analyze_usage.analyze(rows, PRICING, lang="zh-TW")
        self.assertIn("發現", analyze_usage.report_md(zh, "zh-TW"))


class TestLoading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _csv(self, name, text):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def test_map_override_and_missing(self):
        p = self._csv("c.csv", "日期,機器人,輸入\n2026-10-01 10:00:00,a,1000\n2026-10-01 11:00:00,a,1200\n")
        with self.assertRaises(analyze_usage.UsageError):
            analyze_usage.load_rows(p)
        rows, _, _ = analyze_usage.load_rows(p, {"time": "日期", "bot": "機器人", "input": "輸入"})
        self.assertEqual([r["bot"] for r in rows], ["a", "a"])

    def test_cached_separate(self):
        p = self._csv("s.csv", "timestamp,bot,input_tokens,cached_tokens\n2026-10-01T00:00:00Z,a,200,800\n")
        rows, _, _ = analyze_usage.load_rows(p, cached_separate=True)
        self.assertEqual((rows[0]["input"], rows[0]["cached"]), (1000, 800))

    def test_epoch_ms_and_semicolons(self):
        self.assertEqual(analyze_usage.parse_time("1759312800000").year, 2025)
        p = self._csv("semi.csv", "timestamp;bot;input_tokens\n2026-10-01T00:00:00Z;a;5\n")
        self.assertEqual(len(analyze_usage.load_rows(p)[0]), 1)

    def test_split_resets(self):
        days = list(range(8))
        ctx = [10_000, 20_000, 30_000, 40_000, 12_000, 22_000, 32_000, 31_000]
        self.assertEqual(analyze_usage.split_resets(days, ctx), [(0, 4), (4, 8)])
        self.assertEqual(analyze_usage.split_resets(days, [10, 20, 5]), [(0, 3)])   # tiny drops ignored


if __name__ == "__main__":
    unittest.main()
