"""Consultant layer: cost anatomy, data gaps, trend, ranked plan and the one-page memo."""
import io
import json
import math
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import timezone, timedelta

from _paths import ROOT  # noqa: F401
import advise
import analyze_usage
import cli
import make_grok_export
import make_sample_usage
import orgopt

PRICING = orgopt.load_pricing("grok-4.7")


class TestGrokExportMemo(unittest.TestCase):
    """The common real-world case: no Automation ID, no chat id, plan-billed, one dominant bucket."""

    @classmethod
    def setUpClass(cls):
        cls.csv = os.path.join(tempfile.mkdtemp(), "usage-events.csv")
        make_grok_export.generate(cls.csv)
        cls.rep = advise.report(cls.csv, PRICING, goal_usd_week=60, tz=advise.parse_tz("+08:00"))
        cls.res = cls.rep["analysis"]

    def test_empty_bot_column_falls_back_to_model(self):
        info = self.res["load_info"]
        self.assertEqual(self.res["column_mapping"]["bot"], "Automation ID")
        self.assertEqual(info["bot_column_empty"], "Automation ID")
        self.assertEqual(info["bot_empty_rows"], info["rows_loaded"])
        self.assertEqual(info["cloud_agent_rows"], 4)
        names = [u["unit"] for u in self.res["units"]]
        self.assertEqual(names, ["grok-bot-default", "grok-bot-automation"])
        self.assertTrue(all(u["from_model"] for u in self.res["units"]))

    def test_gaps(self):
        codes = {g["code"]: g["severity"] for g in self.rep["gaps"]}
        self.assertEqual(codes["bot_empty"], "high")
        self.assertIn("no_chat", codes)
        self.assertIn("plan_billed", codes)
        self.assertNotIn("no_cache", codes)
        self.assertEqual(self.rep["plan"]["confidence"], "low")

    def test_sawtooth_and_percentiles(self):
        u = self.res["units"][0]
        self.assertTrue(u["sawtooth"])
        self.assertLess(u["C_floor"], 75_000)
        self.assertGreater(u["C_peak"], 130_000)
        self.assertLess(u["C_p10"], u["C_p50"])
        self.assertLess(u["C_p50"], u["C_p90"])

    def test_anatomy_adds_up(self):
        an = self.res["anatomy"]
        self.assertAlmostEqual(an["uncached_input_usd"] + an["cached_input_usd"] + an["output_usd"],
                               self.res["total_usd"], places=6)
        self.assertGreater(an["uncached_input_usd"], an["output_usd"])

    def test_trend_sees_more_wakeups(self):
        ch = {c["unit"]: c for c in self.rep["trend"]["changes"]}["grok-bot-default"]
        self.assertGreater(ch["wakes_day_after"], 1.3 * ch["wakes_day_before"])
        self.assertEqual(self.rep["trend"]["tz"], "UTC+08:00")

    def test_plan(self):
        pl = self.rep["plan"]
        codes = [a["code"] for a in pl["actions"]]
        self.assertEqual(codes[0], "handover")                 # biggest $ × confidence ÷ effort
        self.assertIn("hotspot", codes)
        self.assertIn("ack", codes)
        hot = next(a for a in pl["actions"] if a["code"] == "hotspot")
        self.assertEqual(hot["tier"], "judgment")
        self.assertIn("Find out which bots", hot["title"])
        scores = [a["score"] for a in pl["actions"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        # judgment ranges never enter the combined total, and it never exceeds spend
        self.assertLessEqual(pl["combined_high"], pl["usd_per_week"])
        no_judgment = sum(a["usd_week_high"] for a in pl["actions"] if a["tier"] != "judgment")
        self.assertAlmostEqual(pl["combined_high"], no_judgment)
        for a in pl["actions"]:
            self.assertLessEqual(a["usd_week_low"], a["usd_week_high"])
            self.assertGreaterEqual(a["usd_week_high"], advise.MIN_SHARE * pl["usd_per_week"])

    def test_handover_uses_reset_model(self):
        a = next(a for a in self.rep["plan"]["actions"] if a["code"] == "handover")
        u = self.res["units"][0]
        avg_new = (advise.FRESH_BASE + orgopt.DEFAULT_HANDOVER + 60000) / 2
        r = orgopt.reset_decision(u["C_p50"], avg_new - orgopt.DEFAULT_HANDOVER, orgopt.DEFAULT_HANDOVER,
                                  u["wakes_per_day"], 7, u["rounds_per_wake"], u["cache_hit"], PRICING)
        self.assertAlmostEqual(a["per_unit"][0]["usd_week"], r["net_saving_usd"])
        self.assertEqual(a["confidence"], "low")                # calibration is off on mixed chats

    def test_memo_sections_both_languages(self):
        en = advise.memo_md(self.rep, "en")
        for h in ("## Bottom line", "## Where the money goes", "## Recommendations",
                  "## Decisions and data needed", "## How we will know it worked", "Appendix"):
            self.assertIn(h, en)
        self.assertIn("$60/week", en)
        self.assertIn("2026-10-26", en)                         # check date = end + 7 days (local)
        rep_zh = advise.report(self.csv, PRICING, goal_usd_week=60, lang="zh-TW")
        zh = advise.memo_md(rep_zh, "zh-TW")
        for h in ("## 結論", "## 錢花在哪裡", "## 建議", "## 需要的決定與資料", "## 怎麼確認有效"):
            self.assertIn(h, zh)

    def test_memo_has_no_plumbing(self):
        en = advise.memo_md(self.rep, "en")
        for word in ("PASS", "FAIL", "pytest", "unittest", "core/", ".py"):
            self.assertNotIn(word, en)


class TestSampleMemo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.csv = os.path.join(tempfile.mkdtemp(), "u.csv")
        make_sample_usage.generate(cls.csv)
        cls.rep = advise.report(cls.csv, PRICING)

    def test_good_data_has_no_gaps(self):
        self.assertEqual(self.rep["gaps"], [])
        self.assertEqual(self.rep["plan"]["confidence"], "high")

    def test_planted_patterns_become_actions(self):
        codes = {a["code"] for a in self.rep["plan"]["actions"]}
        self.assertTrue({"read_first", "ack", "cache", "handover"} <= codes)
        ack = next(a for a in self.rep["plan"]["actions"] if a["code"] == "ack")
        self.assertIn("writer", ack["why"])

    def test_goal_reporting(self):
        pl = advise.plan(self.rep["analysis"], self.rep["trend"], self.rep["gaps"], PRICING, goal_usd_week=1)
        g = pl["goal"]
        self.assertFalse(g["met"])
        self.assertFalse(g["reachable"])
        self.assertAlmostEqual(g["remaining"], g["need"] - pl["combined_low"])
        pl = advise.plan(self.rep["analysis"], self.rep["trend"], self.rep["gaps"], PRICING, goal_usd_week=1e6)
        self.assertTrue(pl["goal"]["met"])


class TestTrendAndHelpers(unittest.TestCase):
    def _csv(self, text):
        p = os.path.join(tempfile.mkdtemp(), "t.csv")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def test_same_window(self):
        # 100K uncached input = $0.20. Yesterday at 10:00 and 20:00, today at 10:00 (local +08:00)
        p = self._csv("timestamp,bot,input_tokens\n"
                      "2026-10-18T02:00:00Z,a,100000\n"     # 10:00 local, 10-18
                      "2026-10-18T12:00:00Z,a,100000\n"     # 20:00 local, 10-18
                      "2026-10-19T02:00:00Z,a,100000\n")    # 10:00 local, 10-19
        rep = advise.report(p, PRICING, tz=advise.parse_tz("+08:00"))
        s = rep["trend"]["same_window"]
        self.assertEqual((s["day"], s["until"]), ("2026-10-19", "10:00"))
        self.assertAlmostEqual(s["today_usd"], 0.2)
        self.assertAlmostEqual(s["yesterday_usd"], 0.2)        # only the 10:00 call counts
        self.assertAlmostEqual(s["change"], 0.0)

    def test_parse_tz(self):
        self.assertEqual(advise.parse_tz("+08:00").utcoffset(None), timedelta(hours=8))
        self.assertEqual(advise.parse_tz("UTC-5").utcoffset(None), timedelta(hours=-5))
        self.assertEqual(advise.parse_tz("+05:30").utcoffset(None), timedelta(hours=5, minutes=30))
        self.assertIs(advise.parse_tz("UTC"), timezone.utc)
        with self.assertRaises(ValueError):
            advise.parse_tz("Taipei")

    def test_percentile(self):
        self.assertEqual(analyze_usage.percentile([5], 0.9), 5)
        self.assertAlmostEqual(analyze_usage.percentile([0, 10], 0.5), 5)
        self.assertAlmostEqual(analyze_usage.percentile(list(range(11)), 0.9), 9)

    def test_empty_chat_column_and_short_period(self):
        p = self._csv("timestamp,bot,chat_id,input_tokens,cached_tokens\n"
                      "2026-10-01T00:00:00Z,a,,1000,0\n2026-10-01T05:00:00Z,a,,1000,0\n")
        info = {}
        analyze_usage.load_rows(p, info=info)
        self.assertEqual(info["chat_column_empty"], "chat_id")
        codes = {g["code"] for g in advise.report(p, PRICING)["gaps"]}
        self.assertTrue({"no_chat", "short"} <= codes)

    def test_cost_parts_long_tier(self):
        r = {"input": 250_000, "cached": 50_000, "output": 1000, "reasoning": 0}
        unc, cached, out, prem = analyze_usage.cost_parts(r, PRICING)
        self.assertAlmostEqual(unc + cached + out, orgopt.call_cost_usd(250_000, 50_000, 1000, PRICING))
        self.assertAlmostEqual(prem, (unc + cached + out) / 2)   # the long tier doubles every price
        self.assertEqual(analyze_usage.cost_parts(dict(r, input=100_000), PRICING)[3], 0.0)


class TestCli(unittest.TestCase):
    def test_report_and_trend_commands(self):
        d = tempfile.mkdtemp()
        csv_path = make_grok_export.generate(os.path.join(d, "g.csv"))
        out = os.path.join(d, "memo.md")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["report", csv_path, "--platform", "grok-bot", "--goal-usd-week", "60",
                           "--out", out, "--lang", "zh-TW"])
        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as f:
            self.assertIn("## 結論", f.read())
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(cli.main(["trend", csv_path, "--tz", "+08:00", "--json"]), 0)
        tr = json.loads(buf.getvalue())
        self.assertEqual(tr["tz"], "UTC+08:00")
        self.assertTrue(tr["days"][0]["partial"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(cli.main(["report", csv_path, "--json"]), 0)
        self.assertFalse(math.isnan(json.loads(buf.getvalue())["plan"]["usd_per_week"]))


class TestSetupGuide(unittest.TestCase):
    """The step-by-step guides quote the instructions' anchors and the acceptance-test answer; keep them true."""
    ADAPTER = os.path.join(ROOT, "adapters", "grok-bot")

    def _read(self, name):
        with open(os.path.join(self.ADAPTER, name), encoding="utf-8") as f:
            return f.read()

    def test_instruction_anchors(self):
        for lang in ("en", "zh-TW"):
            text = self._read(f"ADVISOR_BOT.{lang}.md")
            first = text.splitlines()[0]
            last_h2 = [ln for ln in text.splitlines() if ln.startswith("## ")][-1]
            for guide in ("SETUP.en.md", "SETUP.zh-TW.md"):
                g = self._read(guide)
                self.assertIn(first, g, (lang, guide))
                self.assertIn(last_h2, g, (lang, guide))

    def test_acceptance_answer(self):
        csv_path = make_grok_export.generate(os.path.join(tempfile.mkdtemp(), "g.csv"))
        rep = advise.report(csv_path, PRICING, goal_usd_week=60, tz=advise.parse_tz("+08:00"))
        pl = rep["plan"]
        self.assertEqual(round(pl["usd_per_week"]), 80)
        self.assertEqual(rep["analysis"]["units"][0]["unit"], "grok-bot-default")
        self.assertEqual(round(rep["analysis"]["units"][0]["share"] * 100), 96)
        self.assertEqual((round(pl["combined_low"]), round(pl["combined_high"])), (28, 57))
        self.assertTrue(pl["goal"]["reachable"])
        self.assertEqual(pl["confidence"], "low")
        self.assertEqual(pl["actions"][0]["code"], "handover")
        for guide in ("SETUP.en.md", "SETUP.zh-TW.md"):
            g = self._read(guide)
            for needle in ("$80", "96%", "$28–57", "60,000", "bto 0.3.0"):
                self.assertIn(needle, g, (guide, needle))


if __name__ == "__main__":
    unittest.main()
