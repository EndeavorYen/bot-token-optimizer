"""Consultant layer: cost anatomy, data gaps, trend, ranked plan and the one-page memo."""
import csv
import io
import json
import math
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone, timedelta

from _paths import ROOT  # noqa: F401
import advise
import analyze_usage
import cli
import make_grok_export
import make_sample_usage
import orgopt
import schedules

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
        self.assertGreater(ch["wakes_day_after"], 1.1 * ch["wakes_day_before"])
        self.assertEqual(self.rep["trend"]["tz"], "UTC+08:00")

    def test_plan(self):
        pl = self.rep["plan"]
        codes = [a["code"] for a in pl["actions"]]
        self.assertIn("ack", codes)
        # a mixed bucket fills many chats at once: the hand-over lever is either taken or reported as
        # blocked, never dropped
        hv = pl["handover"]
        self.assertTrue("handover" in codes or hv["blocked"])
        # a bucket that mixes bots is the next analysis step, not a generic action handed to the client
        self.assertNotIn("hotspot", codes)
        self.assertEqual(pl["analysis_next"]["kind"], "split_bucket")
        self.assertIn("--schedules", advise.memo_md(self.rep, "en"))
        # every action that changes a bot comes with a paste-ready request
        for a in pl["actions"]:
            if a["code"] in ("handover", "ack", "read_first"):
                self.assertTrue(a["request"])
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
        """Each context's saving = re-reads saved over the week minus one rebuild per hand-over."""
        hv = self.rep["plan"]["handover"]
        p = (hv["picks"] + hv["blocked"])[0]
        e = p["entity"]
        fresh = advise.FRESH_BASE + orgopt.DEFAULT_HANDOVER
        wpd = e["wakes_per_day"] * (1 - e["short_wakes"] / e["wakes"])   # after removing acknowledgements
        r = orgopt.reset_decision(e["C_p50"], (fresh + p["at"]) / 2 - orgopt.DEFAULT_HANDOVER,
                                  orgopt.DEFAULT_HANDOVER, wpd, 7, e["rounds_per_wake"], e["cache_hit"], PRICING)
        n = max(1.0, e["growth_week"] / (p["at"] - fresh))
        self.assertAlmostEqual(p["handovers_week"], n)
        net = orgopt.to_usd(r["saving_per_wake_eq"] * r["expected_wakes"] - n * r["rebuild_cost_eq"], PRICING)
        self.assertAlmostEqual(p["usd_week"], net)
        if hv["blocked"]:
            self.assertIn(p["name"], advise.memo_md(self.rep, "en").split("## Decisions")[1])

    def test_threshold_sweep(self):
        hv = self.rep["plan"]["handover"]
        ats = [row["at"] for row in hv["sweep"]]
        self.assertEqual(ats, sorted(ats))
        # a lower threshold always means more hand-overs
        counts = [row["handovers_week"] for row in hv["sweep"]]
        self.assertEqual(counts, sorted(counts, reverse=True))
        for p in hv["picks"]:
            self.assertLessEqual(p["handovers_week"], hv["max_per_week"])
        # a tighter weekly limit never removes the lever silently: it moves thresholds up or reports
        # the context as blocked, with what it would save
        tight = advise.plan(self.res, self.rep["trend"], self.rep["gaps"], PRICING, max_handovers_week=1.5)
        th = tight["handover"]
        for p in th["picks"]:
            self.assertLessEqual(p["handovers_week"], 1.5)
        names = {p["name"] for p in th["picks"]} | {b["name"] for b in th["blocked"]}
        self.assertEqual(names, {p["name"] for p in hv["picks"]} | {b["name"] for b in hv["blocked"]})
        if th["blocked"]:
            rep = dict(self.rep, plan=tight)
            self.assertIn("hand-overs a week", advise.memo_md(rep, "en"))

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


SCHEDULES = os.path.join(ROOT, "samples", "grok_schedules.txt")


class TestScheduleParser(unittest.TestCase):
    A = datetime(2026, 10, 12, tzinfo=timezone.utc)          # a Monday
    TZ = timezone(timedelta(hours=8))

    def _n(self, line, days=7):
        sp = schedules.parse_line(line)
        return len(schedules.fire_times(sp, self.A, self.A + timedelta(days=days) - timedelta(minutes=1), self.TZ))

    def test_kinds(self):
        self.assertEqual(self._n("m every 30m"), 336)
        self.assertEqual(self._n("m every 2h 08:00-22:00"), 56)          # 08, 10, ..., 22 local
        self.assertEqual(self._n("d at 09:00,18:00"), 14)
        self.assertEqual(self._n("c cron */15 9-18 * * 1-5"), 200)       # 40 a day, Mon-Fri
        self.assertEqual(self._n("w cron 0 10 * * 1"), 1)
        self.assertEqual(self._n("x cron 0 0 1 * 1", days=31), 5)        # cron: day 1 OR Monday

    def test_options_and_intervals(self):
        sp = schedules.parse_line("p every 2h in=grok-bot-default")
        self.assertEqual((sp["in"], sp["text"]), ("grok-bot-default", "every 2h"))
        self.assertEqual(schedules.interval_minutes(sp), 120)
        self.assertEqual(schedules.interval_minutes(schedules.parse_line("d at 09:00,18:00")), 720)
        self.assertAlmostEqual(schedules.interval_minutes(schedules.parse_line("c cron */15 9-18 * * 1-5")), 50.4)
        self.assertEqual(schedules.nice_interval(83), 120)
        self.assertEqual(schedules.nice_interval(2000), 1440)
        self.assertEqual(schedules.fmt_interval(180), "3h")

    def test_errors_are_listed_together(self):
        with self.assertRaises(schedules.ScheduleError) as cm:
            schedules.parse("a every 30m\nb sometimes\nc cron 1 2 3\na at 09:00\nd every 0m\n")
        msg = str(cm.exception)
        for part in ("line 2", "line 3", "line 5", "duplicate bot names: a"):
            self.assertIn(part, msg)


class TestScheduleAttribution(unittest.TestCase):
    """The export has no bot id, but scheduled bots wake at known times: the bucket can be split."""

    @classmethod
    def setUpClass(cls):
        cls.csv = make_grok_export.generate(os.path.join(tempfile.mkdtemp(), "g.csv"))
        cls.rep = advise.report(cls.csv, PRICING, goal_usd_week=60, tz=advise.parse_tz("+08:00"),
                                schedule_specs=schedules.load(SCHEDULES))
        cls.att = cls.rep["attribution"]
        cls.bots = {b["name"]: b for b in cls.att["bots"]}

    def test_matches(self):
        m, d, h = self.bots["monitor"], self.bots["digest"], self.bots["heartbeat"]
        self.assertGreaterEqual(m["matched"], 300)
        self.assertEqual((d["matched"], d["fires"]), (14, 14))
        self.assertEqual((h["matched"], h["fires"]), (168, 168))
        self.assertTrue(all(b["reliable"] for b in self.att["bots"]))
        self.assertEqual(m["units"], ["grok-bot-default"])
        self.assertEqual(h["units"], ["grok-bot-automation"])
        self.assertTrue(0.6 < m["stats"]["idle_share"] < 0.8)           # planted: ~75% nothing to do
        self.assertEqual(d["stats"]["idle_share"], 0.0)
        self.assertEqual([r["unit"] for r in self.att["remainder"]], ["grok-bot-default"])

    def test_spend_is_split_not_invented(self):
        total = sum(b["stats"]["usd"] for b in self.att["bots"]) + sum(r["stats"]["usd"] for r in self.att["remainder"])
        self.assertAlmostEqual(total, self.rep["analysis"]["total_usd"], places=6)

    def test_frequency_actions(self):
        acts = {a["unit"]: a for a in self.rep["plan"]["actions"] if a["code"] == "frequency"}
        self.assertEqual(set(acts), {"monitor", "heartbeat"})
        self.assertEqual(self.rep["plan"]["actions"][0]["unit"], "monitor")
        m = acts["monitor"]
        self.assertEqual(m["new_interval_min"], 120)
        self.assertIn("every 2h", m["request"])
        st = self.bots["monitor"]["stats"]
        self.assertAlmostEqual(m["usd_week_high"], st["usd_week"] * (1 - 30 / 120))
        self.assertEqual(acts["heartbeat"]["new_interval_min"], 180)

    def test_no_double_counting(self):
        pl = self.rep["plan"]
        codes = [a["code"] for a in pl["actions"]]
        self.assertNotIn("ack", codes)               # the idle checks are already removed by the schedules
        self.assertNotIn("hotspot", codes)
        ho = next(a for a in pl["actions"] if a["code"] == "handover")
        mon = next(p for p in ho["per_unit"] if p["unit"] == "monitor")
        e = next(e for e in pl["entities"] if e["name"] == "monitor")
        fresh = advise.FRESH_BASE + orgopt.DEFAULT_HANDOVER
        r = orgopt.reset_decision(e["C_p50"], (fresh + mon["at"]) / 2 - orgopt.DEFAULT_HANDOVER,
                                  orgopt.DEFAULT_HANDOVER, e["wakes_per_day"] * 30 / 120, 7,
                                  e["rounds_per_wake"], e["cache_hit"], PRICING)
        n = max(1.0, e["growth_week"] * 30 / 120 / (mon["at"] - fresh))     # fewer wake-ups fill it slower
        self.assertAlmostEqual(mon["handovers_week"], n)
        net = orgopt.to_usd(r["saving_per_wake_eq"] * r["expected_wakes"] - n * r["rebuild_cost_eq"], PRICING)
        self.assertAlmostEqual(mon["usd_week"], net)                       # priced on the wake-ups left
        self.assertLessEqual(pl["combined_high"], pl["usd_per_week"])
        self.assertEqual(pl["analysis_next"]["kind"], "split_remainder")

    def test_confidence_and_gaps(self):
        g = next(g for g in self.rep["gaps"] if g["code"] == "bot_empty")
        self.assertEqual(g["severity"], "medium")
        self.assertEqual(self.rep["plan"]["confidence"], "medium")

    def test_memo(self):
        for lang, inside, paste in (("en", "Who is inside", "Paste to the main bot"),
                                    ("zh-TW", "裡是誰", "貼給主 bot 的變更請求")):
            rep = advise.report(self.csv, PRICING, goal_usd_week=60, tz=advise.parse_tz("+08:00"),
                                schedule_specs=schedules.load(SCHEDULES), lang=lang)
            md = advise.memo_md(rep, lang)
            self.assertIn(inside, md)
            self.assertIn(paste, md)
            self.assertIn("monitor", md.split("##")[1])                      # named in the bottom line
            self.assertIn("```text", md)


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
        # the README quick starts quote the same anchors and the current version
        import importlib
        version = importlib.import_module("cli").__version__
        for readme, lang in (("README.md", "en"), ("README.zh-TW.md", "zh-TW")):
            with open(os.path.join(ROOT, readme), encoding="utf-8") as f:
                text = f.read()
            playbook = self._read(f"ADVISOR_BOT.{lang}.md").splitlines()
            self.assertIn(playbook[0], text, readme)
            self.assertIn([ln for ln in playbook if ln.startswith("## ")][-1], text, readme)
            # no version is written out by hand (it would go stale); the badge reads pyproject.toml
            self.assertIn("pyproject.toml&query=%24.project.version", text, readme)
            self.assertNotIn(f"bto {version}", text, readme)
        for guide in ("SETUP.en.md", "SETUP.zh-TW.md"):
            self.assertIsNone(__import__("re").search(r"bto \d+\.\d+\.\d+", self._read(guide)), guide)

    def test_acceptance_answer(self):
        csv_path = make_grok_export.generate(os.path.join(tempfile.mkdtemp(), "g.csv"))
        rep = advise.report(csv_path, PRICING, tz=advise.parse_tz("+08:00"), quota_days=3,
                            schedule_specs=schedules.load(SCHEDULES))
        pl, q = rep["plan"], rep["plan"]["quota"]
        bots = {b["name"]: b for b in rep["attribution"]["bots"]}
        self.assertEqual((round(pl["usd_per_week"]), round(q["budget_usd_week"])), (158, 68))
        self.assertEqual(round(bots["monitor"]["share"] * 100), 50)
        self.assertEqual(round(bots["monitor"]["stats"]["idle_share"] * 100), 64)
        self.assertEqual(round(bots["monitor"]["stats"]["C_p50"], -4), 110_000)
        first = pl["actions"][0]
        self.assertEqual((first["code"], first["unit"], first["new_interval_min"]), ("frequency", "monitor", 120))
        self.assertEqual((round(first["usd_week_low"]), round(first["usd_week_high"])), (41, 59))
        ho = next(a for a in pl["actions"] if a["code"] == "handover")
        self.assertEqual({p["unit"]: p["at"] for p in ho["per_unit"]}["monitor"], 40000)
        self.assertEqual((round(ho["usd_week_low"]), round(ho["usd_week_high"])), (25, 50))
        self.assertEqual((round(pl["combined_low"]), round(pl["combined_high"])), (72, 119))
        self.assertEqual((round(q["days_after_low"], 1), round(q["days_after_high"], 1)), (5.5, 12.0))
        for guide in ("SETUP.en.md", "SETUP.zh-TW.md"):
            g = self._read(guide)
            for needle in ("$158", "$68", "64%", "$41–59", "$72–119", "5.5", "grok_schedules.txt"):
                self.assertIn(needle, g, (guide, needle))
        for playbook in ("ADVISOR_BOT.en.md", "ADVISOR_BOT.zh-TW.md"):
            g = self._read(playbook)
            for needle in ("$158", "$68", "64%", "$41–59", "$25–50", "$72–119", "5.5", "$70", "bto quota"):
                self.assertIn(needle, g, (playbook, needle))


class TestQuota(unittest.TestCase):
    def test_math(self):
        q = advise.quota_math(2536, 3, 7, 560, 630)
        self.assertAlmostEqual(q["budget_usd_week"], 2536 * 3 / 7)
        self.assertAlmostEqual(q["quota_usd"], 2536 * 3 / 7)
        self.assertAlmostEqual(q["days_after_low"], (2536 * 3 / 7) / ((2536 - 560) / 7))
        self.assertEqual((round(q["days_after_low"], 1), round(q["days_after_high"], 1)), (3.9, 4.0))
        self.assertTrue(math.isinf(advise.quota_math(100, 3, 7, 100)["days_after_low"]))
        with self.assertRaises(ValueError):
            advise.quota_math(100, 0)

    def test_cli(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(cli.main(["quota", "--week-usd", "2536", "--days-now", "3", "--save", "560", "630",
                                       "--lang", "zh-TW"]), 0)
        self.assertIn("3.9–4.0 天", buf.getvalue())
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["quota", "--week-usd", "2536", "--days-now", "3", "--json"])
        self.assertAlmostEqual(json.loads(buf.getvalue())["budget_usd_week"], 2536 * 3 / 7)

    def test_report_with_quota(self):
        csv_path = make_grok_export.generate(os.path.join(tempfile.mkdtemp(), "g.csv"))
        rep = advise.report(csv_path, PRICING, quota_days=3, target_days=7)
        md = advise.memo_md(rep, "en")
        self.assertIn("the quota lasts 3 days", md)
        self.assertIn("Days the quota lasts", md)
        self.assertAlmostEqual(rep["plan"]["goal"]["target"], rep["plan"]["usd_per_week"] * 3 / 7)


GROK_START = datetime(2026, 10, 12, tzinfo=timezone.utc)


def _drop_window(src, dst, start, lo_days, hi_days):
    """Copy a usage CSV without rows in [start+lo, start+hi)."""
    lo = start + timedelta(days=lo_days)
    hi = start + timedelta(days=hi_days)
    with open(src, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    kept = [rows[0]]
    for r in rows[1:]:
        t = datetime.fromisoformat(r[0].replace("Z", "+00:00"))
        if lo <= t < hi:
            continue
        kept.append(r)
    with open(dst, "w", newline="", encoding="utf-8") as f:
        csv.writer(f, quoting=csv.QUOTE_ALL).writerows(kept)
    return dst


def _uniform_gap_csv(path):
    """Hourly calls, one 72 h zero-row stretch, constant token size. No real user data."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    stamps = [start + timedelta(hours=h) for h in range(48)]
    stamps += [start + timedelta(hours=119 + h) for h in range(25)]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "bot", "chat_id", "input_tokens", "cached_input_tokens", "output_tokens"])
        for t in stamps:
            w.writerow([t.strftime("%Y-%m-%dT%H:%M:%SZ"), "meter", "c1", 1000, 200, 50])
    return path, start


class TestQuotaExhaustionGap(unittest.TestCase):
    """A multi-day zero-row stretch is quota exhaustion, not a slower week."""

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.mkdtemp()
        cls.tz = advise.parse_tz("+08:00")
        cls.full = make_grok_export.generate(os.path.join(tmp, "full.csv"))
        cls.gap = _drop_window(cls.full, os.path.join(tmp, "gap.csv"), GROK_START, 2.5, 5.5)
        cls.full_rep = advise.report(cls.full, PRICING, tz=cls.tz, quota_days=3)
        cls.gap_rep = advise.report(cls.gap, PRICING, tz=cls.tz, quota_days=3)
        cls.uniform, cls.uniform_start = _uniform_gap_csv(os.path.join(tmp, "uniform.csv"))
        cls.uniform_rep = advise.report(cls.uniform, PRICING, tz=cls.tz)

    def test_weekly_rate_quota_and_savings_match_the_ungapped_export(self):
        full, gap = self.full_rep["plan"], self.gap_rep["plan"]
        self.assertAlmostEqual(gap["usd_per_week"], full["usd_per_week"], delta=0.05 * full["usd_per_week"])
        self.assertEqual(gap["quota"]["days_now"], 3)
        self.assertAlmostEqual(gap["quota"]["budget_usd_week"], full["quota"]["budget_usd_week"],
                               delta=0.05 * full["quota"]["budget_usd_week"])
        for key in ("days_after_low", "days_after_high"):
            self.assertAlmostEqual(gap["quota"][key], full["quota"][key], delta=0.05 * abs(full["quota"][key]) + 0.05)
        full_s = {a["code"]: a["usd_week_high"] for a in full["actions"]}
        gap_s = {a["code"]: a["usd_week_high"] for a in gap["actions"]}
        for code, usd in full_s.items():
            self.assertIn(code, gap_s)
            self.assertAlmostEqual(gap_s[code], usd, delta=0.15 * usd + 0.05)
        self.assertAlmostEqual(gap["combined_low"], full["combined_low"], delta=0.15 * full["combined_low"] + 0.05)
        self.assertAlmostEqual(gap["combined_high"], full["combined_high"], delta=0.15 * full["combined_high"] + 0.05)
        # a passed --quota-days is not replaced by the measured cycle
        self.assertIsNone(self.gap_rep["quota_inferred"])

    def test_report_names_gap_dates_length_and_denominator(self):
        res = self.gap_rep["analysis"]
        self.assertEqual(len(res["usage_gaps"]), 1)
        g = res["usage_gaps"][0]
        self.assertGreater(g["days"], 3.0)
        self.assertLess(g["days"], 3.5)
        self.assertAlmostEqual(res["active_days"], res["span_days"] - g["days"], places=6)
        self.assertGreater(res["span_days"], res["active_days"] + 2)
        md = advise.memo_md(self.gap_rep, "en")
        start = datetime.fromisoformat(g["start"]).astimezone(self.tz).strftime("%Y-%m-%d %H:%M")
        end = datetime.fromisoformat(g["end"]).astimezone(self.tz).strftime("%Y-%m-%d %H:%M")
        self.assertIn(start, md)
        self.assertIn(end, md)
        self.assertIn(f"{g['days']:.1f}", md)
        self.assertIn(f"{res['active_days']:.1f} active days", md)
        self.assertIn(f"{res['span_days']:.1f}", md)
        zh = advise.memo_md(self.gap_rep, "zh-TW")
        self.assertIn("額度缺口", zh)
        self.assertIn(f"{res['active_days']:.1f}", zh)

    def test_trend_lists_missing_days_and_does_not_call_the_gap_a_drop(self):
        tr = self.gap_rep["trend"]
        missing = [e for e in tr["days"] if e["calls"] == 0]
        self.assertGreaterEqual(len(missing), 2)
        self.assertTrue(all(e["nodata"] and e["usd"] == 0 and e["wakes"] == 0 for e in missing))
        text = advise.trend_md(tr, "en")
        self.assertIn("no data", text)
        self.assertIn("active days", text)
        ch = {c["unit"]: c for c in tr["changes"]}["grok-bot-default"]
        self.assertGreater(ch["usd_day_after"], ch["usd_day_before"] * 0.9)

        utr = self.uniform_rep["trend"]
        self.assertGreaterEqual(sum(1 for e in utr["days"] if e["nodata"]), 2)
        uch = utr["changes"][0]
        self.assertLess(abs(uch["usd_day_after"] - uch["usd_day_before"]) / uch["usd_day_before"], 0.1)
        # the same rows over the calendar span would report a much cheaper week
        span = self.uniform_rep["analysis"]["span_days"]
        active = self.uniform_rep["analysis"]["active_days"]
        diluted = self.uniform_rep["analysis"]["total_usd"] * 7 / span
        self.assertLess(diluted, self.uniform_rep["plan"]["usd_per_week"] * 0.7)
        self.assertAlmostEqual(self.uniform_rep["plan"]["usd_per_week"],
                               self.uniform_rep["analysis"]["total_usd"] * 7 / active, places=6)

    def test_synthetic_gap_reports_one_quota_cycle(self):
        res = self.uniform_rep["analysis"]
        self.assertEqual(len(res["quota_cycles"]), 1)
        c = res["quota_cycles"][0]
        per = orgopt.call_cost_usd(1000, 200, 50, PRICING)
        self.assertTrue(c["partial"])
        self.assertAlmostEqual(c["days"], 47 / 24, places=6)
        self.assertEqual(c["tokens"], 48 * 1050)
        self.assertAlmostEqual(c["usd"], 48 * per, places=6)
        # one real gap, but the cycle before it starts at the export boundary (partial),
        # so quota days are not inferred
        self.assertIsNone(self.uniform_rep["plan"]["quota"])
        self.assertIsNone(self.uniform_rep["quota_inferred"])
        md = advise.memo_md(self.uniform_rep, "en")
        self.assertIn("partial cycle", md)
        self.assertNotIn("95% less", md)
        self.assertIn(f"{c['tokens']:,.0f}", md)
        self.assertIn(f"${c['usd']:,.2f}", md)
        self.assertIn(f"{c['days']:.1f}", md)

    def test_gap_shorter_than_max_gap_does_not_change_the_denominator(self):
        wide = advise.report(self.gap, PRICING, tz=self.tz, quota_days=3, max_gap_hours=1000)
        self.assertEqual(wide["analysis"]["usage_gaps"], [])
        self.assertAlmostEqual(wide["analysis"]["active_days"], wide["analysis"]["span_days"])
        self.assertLess(wide["plan"]["usd_per_week"], self.full_rep["plan"]["usd_per_week"] * 0.75)

    def test_export_without_a_gap_is_unchanged(self):
        res = self.full_rep["analysis"]
        self.assertEqual(res["usage_gaps"], [])
        self.assertEqual(res["quota_cycles"], [])
        self.assertAlmostEqual(res["active_days"], res["span_days"])
        md = advise.memo_md(self.full_rep, "en")
        self.assertNotIn("active days", md)
        self.assertNotIn("Quota gap", md)
        self.assertIn("Weekly figures = period × 7 / days.", md)
        text = advise.trend_md(self.full_rep["trend"], "en")
        self.assertNotIn("no data", text)
        self.assertNotIn("active days", text)

    def test_cli_trend_shows_the_gap(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(cli.main(["trend", self.gap, "--tz", "+08:00", "--max-gap", "12"]), 0)
        self.assertIn("no data", buf.getvalue())
        self.assertIn("active days", buf.getvalue())


def _write_calls(path, stamps):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "bot", "chat_id", "input_tokens", "cached_input_tokens", "output_tokens"])
        for t in stamps:
            stamp = t.astimezone(timezone.utc) if t.tzinfo else t
            w.writerow([stamp.strftime("%Y-%m-%dT%H:%M:%SZ"), "meter", "c1", 1000, 200, 50])
    return path


def _long_silences(path, hours=12):
    rows, _, _ = analyze_usage.load_rows(path)
    limit = hours * 3600
    return [(b["time"] - a["time"]).total_seconds()
            for a, b in zip(rows, rows[1:])
            if (b["time"] - a["time"]).total_seconds() > limit]


def _office_hours_csv(path, days=14):
    """Weekday calls every 15 minutes, 09:00–18:00 UTC. Nights are 15 h, weekends 63 h."""
    start = datetime(2026, 10, 12, tzinfo=timezone.utc)  # Monday
    stamps = []
    for d in range(days):
        day = start + timedelta(days=d)
        if day.weekday() >= 5:
            continue
        for hour in range(9, 19):
            minutes = (0,) if hour == 18 else (0, 15, 30, 45)
            for minute in minutes:
                stamps.append(day.replace(hour=hour, minute=minute))
    return _write_calls(path, stamps)


def _twice_daily_csv(path, days=14):
    """One call at 09:00 and one at 18:00 every day, including weekends. Nights are 15 h."""
    start = datetime(2026, 10, 12, tzinfo=timezone.utc)
    stamps = []
    for d in range(days):
        day = start + timedelta(days=d)
        for hour in (9, 18):
            stamps.append(day.replace(hour=hour))
    return _write_calls(path, stamps)


class TestScheduledIdleIsNotQuotaGap(unittest.TestCase):
    """Overnight and weekend quiet match the export's own clock, so they are not exhaustion."""

    def _assert_idle_keeps_the_span_rate(self, path, night=True, weekend=False):
        silences = _long_silences(path)
        self.assertTrue(silences)
        if night:
            self.assertTrue(any(14 * 3600 <= s <= 16 * 3600 for s in silences))
        if weekend:
            self.assertTrue(any(60 * 3600 <= s <= 66 * 3600 for s in silences))
        rep = advise.report(path, PRICING)
        res = rep["analysis"]
        self.assertEqual(res["usage_gaps"], [])
        self.assertEqual(res["quota_cycles"], [])
        self.assertAlmostEqual(res["active_days"], res["span_days"])
        self.assertAlmostEqual(rep["plan"]["usd_per_week"], res["total_usd"] * 7 / res["span_days"], places=6)
        idle_days = sum(silences) / 86400
        buggy = res["total_usd"] * 7 / (res["span_days"] - idle_days)
        self.assertGreater(buggy, rep["plan"]["usd_per_week"] * 1.2)
        self.assertIsNone(rep["plan"]["quota"])
        self.assertIsNone(rep["quota_inferred"])
        md = advise.memo_md(rep, "en")
        self.assertIn("Quota days are omitted", md)
        self.assertNotIn("Quota gap", md)
        self.assertNotIn("0.375", md)
        self.assertNotIn("95% less", md)
        self.assertIn("Weekly figures = period × 7 / days.", md)

    def test_weekday_office_hours_nights_and_weekends_are_not_gaps(self):
        path = _office_hours_csv(os.path.join(tempfile.mkdtemp(), "office.csv"))
        self._assert_idle_keeps_the_span_rate(path, night=True, weekend=True)

    def test_twice_daily_nights_are_not_gaps(self):
        path = _twice_daily_csv(os.path.join(tempfile.mkdtemp(), "twice.csv"))
        self._assert_idle_keeps_the_span_rate(path, night=True, weekend=False)

    def test_complete_cycle_quota_days_are_rounded(self):
        """Two exhaustion holes close one full cycle; the printed quota days are rounded to 1 decimal."""
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        stamps, t = [], start
        for hours, pause in ((48, 72), (48, 72), (24, 0)):
            for _ in range(hours):
                stamps.append(t)
                t += timedelta(hours=1)
            t += timedelta(hours=pause - 1) if pause else timedelta(0)
        path = _write_calls(os.path.join(tempfile.mkdtemp(), "cycles.csv"), stamps)
        rep = advise.report(path, PRICING)
        cycles = rep["analysis"]["quota_cycles"]
        self.assertEqual(len(rep["analysis"]["usage_gaps"]), 2)
        self.assertEqual([c["partial"] for c in cycles], [True, False])
        raw = cycles[1]["days"]
        self.assertNotEqual(raw, round(raw, 1))
        self.assertEqual(rep["quota_inferred"]["days"], round(raw, 1))
        self.assertFalse(rep["quota_inferred"]["partial"])
        self.assertEqual(rep["plan"]["quota"]["days_now"], round(raw, 1))
        md = advise.memo_md(rep, "en")
        self.assertIn(f"Quota days ({round(raw, 1):.1f})", md)
        self.assertNotIn(f"{raw:.5f}", md)


def _zoned_office_csv(path, offset_hours, weekdays, outage=None, days=21):
    """Calls every 15 min, 09:00–18:45 local, `days` calendar days from Monday 2026-10-05.

    `weekdays` is a set of weekday numbers (Monday is 0). `outage` is a (start, end) pair of
    local datetimes; calls in that half-open interval are dropped."""
    tz = timezone(timedelta(hours=offset_hours)) if offset_hours else timezone.utc
    start = datetime(2026, 10, 5, tzinfo=tz)
    stamps = []
    for d in range(days):
        day = start + timedelta(days=d)
        if day.weekday() not in weekdays:
            continue
        for hour in range(9, 19):
            for minute in (0, 15, 30, 45):
                t = day.replace(hour=hour, minute=minute)
                if outage and outage[0] <= t < outage[1]:
                    continue
                stamps.append(t)
    return _write_calls(path, stamps)


def _two_date_hole_csv(path):
    """8 h of calls, a 34 h hole, then 4 h. Rows fall on two dates."""
    start = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)
    stamps = []
    t = start
    end = start + timedelta(hours=8)
    while t < end:
        stamps.append(t)
        t += timedelta(minutes=15)
    t = end + timedelta(hours=34)
    end = t + timedelta(hours=4)
    while t < end:
        stamps.append(t)
        t += timedelta(minutes=15)
    return _write_calls(path, stamps)


REST_UNJUDGED_EN = (
    "Rest days were not judged: the export has fewer than two full weeks, "
    "so only silences that start or end inside busy hours count as quota gaps."
)
REST_UNJUDGED_ZH = "未判斷休息日：資料不到兩個完整週，只有在忙碌時段內開始或結束的靜默才算額度缺口。"


class TestLocalClockGaps(unittest.TestCase):
    """Quota gaps use --tz, or the quiet-point frame when it is omitted. t1–t14."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        mon_fri = {0, 1, 2, 3, 4}
        mon_sat = {0, 1, 2, 3, 4, 5}
        mon_thu = {0, 1, 2, 3}
        cls.t1 = _zoned_office_csv(os.path.join(cls.tmp, "t1.csv"), -7, mon_fri)
        cls.t2 = _zoned_office_csv(os.path.join(cls.tmp, "t2.csv"), 10, mon_fri)
        cls.t3 = _zoned_office_csv(os.path.join(cls.tmp, "t3.csv"), 0, mon_sat)
        cls.t4 = _zoned_office_csv(os.path.join(cls.tmp, "t4.csv"), 0, mon_thu)
        cls.t5_sun = _zoned_office_csv(os.path.join(cls.tmp, "t5sun.csv"), -7, mon_sat)
        cls.t5_fri = _zoned_office_csv(os.path.join(cls.tmp, "t5fri.csv"), 10, mon_thu)
        cls.t6 = _office_hours_csv(os.path.join(cls.tmp, "t6.csv"))
        cls.full = make_grok_export.generate(os.path.join(cls.tmp, "full.csv"))
        cls.issue4 = _drop_window(cls.full, os.path.join(cls.tmp, "issue4.csv"), GROK_START, 2.5, 5.5)
        cls.t9 = {}
        for hours in (-7, 0, 8, 10):
            tz = timezone(timedelta(hours=hours)) if hours else timezone.utc
            outage = (datetime(2026, 10, 13, 13, 0, tzinfo=tz), datetime(2026, 10, 15, 12, 0, tzinfo=tz))
            cls.t9[hours] = _zoned_office_csv(os.path.join(cls.tmp, f"t9_{hours}.csv"), hours, mon_fri, outage)
        cls.t10 = _two_date_hole_csv(os.path.join(cls.tmp, "t10.csv"))
        cls.t11 = _zoned_office_csv(os.path.join(cls.tmp, "t11.csv"), -7, {1, 3, 5, 6})
        cls.t12 = _zoned_office_csv(os.path.join(cls.tmp, "t12.csv"), 10, {0, 2, 4, 5, 6})
        t13_tz = timezone(timedelta(hours=-7))
        t13_outage = (datetime(2026, 10, 14, 0, 0, tzinfo=t13_tz), datetime(2026, 10, 15, 12, 0, tzinfo=t13_tz))
        cls.t13 = _zoned_office_csv(os.path.join(cls.tmp, "t13.csv"), -7, mon_fri, t13_outage)

    def _assert_scheduled_idle(self, path, tz):
        """Matching tz and no tz: no gaps, span rate, no inferred quota."""
        weeks = []
        for chosen in (tz, None):
            rep = advise.report(path, PRICING, **({} if chosen is None else {"tz": chosen}))
            res = rep["analysis"]
            self.assertEqual(res["usage_gaps"], [])
            self.assertEqual(res["quota_cycles"], [])
            self.assertAlmostEqual(res["active_days"], res["span_days"])
            self.assertAlmostEqual(rep["plan"]["usd_per_week"], res["total_usd"] * 7 / res["span_days"], places=6)
            self.assertIsNone(rep["quota_inferred"])
            self.assertIsNone(rep["plan"]["quota"])
            self.assertNotIn("quota lasts", advise.memo_md(rep, "en"))
            weeks.append(rep["plan"]["usd_per_week"])
        self.assertAlmostEqual(weeks[0], weeks[1], places=6)

    def _frame_hours(self, path):
        rows, _, _ = analyze_usage.load_rows(path)
        frame = analyze_usage._quiet_point_tz(rows)
        _, rest = analyze_usage._activity_clock(rows, frame)
        return int(frame.utcoffset(None).total_seconds() // 3600), rest

    def test_t1_utc_minus_7_weekday_office_is_not_a_gap(self):
        self._assert_scheduled_idle(self.t1, timezone(timedelta(hours=-7)))
        self.assertEqual(self._frame_hours(self.t1), (-9, {5, 6}))

    def test_t2_utc_plus_10_weekday_office_is_not_a_gap(self):
        self._assert_scheduled_idle(self.t2, timezone(timedelta(hours=10)))
        self.assertEqual(self._frame_hours(self.t2), (8, {5, 6}))

    def test_t3_sunday_only_rest_is_not_a_gap(self):
        self._assert_scheduled_idle(self.t3, timezone.utc)
        self.assertEqual(self._frame_hours(self.t3)[1], {6})

    def test_t4_friday_to_sunday_rest_is_not_a_gap(self):
        self._assert_scheduled_idle(self.t4, timezone.utc)
        self.assertEqual(self._frame_hours(self.t4)[1], {4, 5, 6})

    def test_t5_shifted_sunday_and_long_weekend_are_not_gaps(self):
        self._assert_scheduled_idle(self.t5_sun, timezone(timedelta(hours=-7)))
        self._assert_scheduled_idle(self.t5_fri, timezone(timedelta(hours=10)))
        self.assertEqual(self._frame_hours(self.t5_sun), (-9, {6}))
        self.assertEqual(self._frame_hours(self.t5_fri), (8, {4, 5, 6}))

    def test_t6_utc_office_hours_rate_is_unchanged(self):
        rep = advise.report(self.t6, PRICING)
        res = rep["analysis"]
        self.assertEqual(res["usage_gaps"], [])
        self.assertAlmostEqual(rep["plan"]["usd_per_week"], 0.455385, places=6)
        self.assertAlmostEqual(rep["plan"]["usd_per_week"], res["total_usd"] * 7 / res["span_days"], places=6)
        self.assertIsNone(rep["quota_inferred"])
        self.assertIsNone(rep["plan"]["quota"])
        self.assertNotIn("quota lasts", advise.memo_md(rep, "en"))

    def test_t7_issue4_with_quota_days_is_unchanged(self):
        full = advise.report(self.full, PRICING, tz=advise.parse_tz("+08:00"), quota_days=3)
        self.assertAlmostEqual(full["plan"]["usd_per_week"], 158.29, places=2)
        for chosen in (advise.parse_tz("+08:00"), None):
            rep = advise.report(self.issue4, PRICING, quota_days=3, **({} if chosen is None else {"tz": chosen}))
            res = rep["analysis"]
            self.assertEqual(len(res["usage_gaps"]), 1)
            self.assertAlmostEqual(res["usage_gaps"][0]["days"], 3.01, places=2)
            self.assertAlmostEqual(rep["plan"]["usd_per_week"], 157.66, places=2)
            self.assertAlmostEqual(rep["plan"]["usd_per_week"], full["plan"]["usd_per_week"],
                                   delta=0.05 * full["plan"]["usd_per_week"])
            self.assertEqual(rep["plan"]["quota"]["days_now"], 3)
            self.assertIsNone(rep["quota_inferred"])

    def test_t8_issue4_partial_cycle_infers_no_quota(self):
        for chosen in (advise.parse_tz("+08:00"), None):
            rep = advise.report(self.issue4, PRICING, **({} if chosen is None else {"tz": chosen}))
            res = rep["analysis"]
            self.assertEqual(len(res["usage_gaps"]), 1)
            self.assertEqual([c["partial"] for c in res["quota_cycles"]], [True])
            self.assertIsNone(rep["plan"]["quota"])
            self.assertIsNone(rep["quota_inferred"])
            self.assertNotIn("quota lasts", advise.memo_md(rep, "en"))

    def test_t9_midweek_outage_is_one_gap_in_every_zone(self):
        for hours, path in self.t9.items():
            tz = timezone(timedelta(hours=hours)) if hours else timezone.utc
            for chosen in (tz, None):
                rep = advise.report(path, PRICING, **({} if chosen is None else {"tz": chosen}))
                res = rep["analysis"]
                self.assertEqual(len(res["usage_gaps"]), 1, (hours, chosen))
                gap = res["usage_gaps"][0]
                self.assertAlmostEqual(gap["days"], 1.97, places=2, msg=(hours, chosen))
                self.assertAlmostEqual(rep["plan"]["usd_per_week"],
                                       res["total_usd"] * 7 / (res["span_days"] - gap["days"]), places=6)
                self.assertEqual([c["partial"] for c in res["quota_cycles"]], [True])
                self.assertIsNone(rep["plan"]["quota"])
                self.assertIsNone(rep["quota_inferred"])

    def test_t10_two_dates_yield_no_gap(self):
        rows, _, _ = analyze_usage.load_rows(self.t10)
        self.assertEqual(len({r["time"].date() for r in rows}), 2)
        self.assertTrue(any(s > 12 * 3600 for s in _long_silences(self.t10)))
        rep = advise.report(self.t10, PRICING)
        self.assertEqual(rep["analysis"]["usage_gaps"], [])
        self.assertIsNone(rep["plan"]["quota"])
        self.assertIsNone(rep["quota_inferred"])

    def test_t11_split_monday_wednesday_friday_rest_is_not_a_gap(self):
        self._assert_scheduled_idle(self.t11, timezone(timedelta(hours=-7)))
        self.assertEqual(self._frame_hours(self.t11), (-9, {0, 2, 4}))

    def test_t12_split_tuesday_thursday_rest_is_not_a_gap(self):
        self._assert_scheduled_idle(self.t12, timezone(timedelta(hours=10)))
        self.assertEqual(self._frame_hours(self.t12), (8, {1, 3}))

    def test_t13_midweek_quota_gap_stays_one_gap(self):
        for chosen in (timezone(timedelta(hours=-7)), None):
            rep = advise.report(self.t13, PRICING, **({} if chosen is None else {"tz": chosen}))
            res = rep["analysis"]
            self.assertEqual(len(res["usage_gaps"]), 1, chosen)
            gap = res["usage_gaps"][0]
            self.assertAlmostEqual(gap["days"], 1.72, places=2, msg=chosen)
            self.assertAlmostEqual(rep["plan"]["usd_per_week"],
                                   res["total_usd"] * 7 / (res["span_days"] - gap["days"]), places=6)
            self.assertEqual([c["partial"] for c in res["quota_cycles"]], [True])
            self.assertIsNone(rep["plan"]["quota"])
            self.assertIsNone(rep["quota_inferred"])
            self.assertTrue(res["rest_days_judged"])
            self.assertNotIn(REST_UNJUDGED_EN, advise.memo_md(rep, "en"))

    def test_t14_under_two_weeks_warns_and_keeps_the_issue4_gap(self):
        for chosen in (advise.parse_tz("+08:00"), None):
            rep = advise.report(self.issue4, PRICING, **({} if chosen is None else {"tz": chosen}))
            res = rep["analysis"]
            self.assertFalse(res["rest_days_judged"], chosen)
            self.assertEqual(len(res["usage_gaps"]), 1, chosen)
            self.assertAlmostEqual(res["usage_gaps"][0]["days"], 3.01, places=2, msg=chosen)
            self.assertIn(REST_UNJUDGED_EN, advise.memo_md(rep, "en"))
            self.assertIn(REST_UNJUDGED_ZH, advise.memo_md(rep, "zh-TW"))
        for chosen in (timezone(timedelta(hours=-7)), None):
            rep = advise.report(self.t11, PRICING, **({} if chosen is None else {"tz": chosen}))
            self.assertTrue(rep["analysis"]["rest_days_judged"], chosen)
            self.assertNotIn(REST_UNJUDGED_EN, advise.memo_md(rep, "en"))
            self.assertNotIn(REST_UNJUDGED_ZH, advise.memo_md(rep, "zh-TW"))

    def test_report_and_trend_tz_defaults_to_unset(self):
        parser = cli.build_parser()
        self.assertIsNone(parser.parse_args(["report", "x.csv"]).tz)
        self.assertIsNone(parser.parse_args(["trend", "x.csv"]).tz)
        self.assertEqual(parser.parse_args(["report", "x.csv", "--tz", "+10:00"]).tz, "+10:00")


if __name__ == "__main__":
    unittest.main()
