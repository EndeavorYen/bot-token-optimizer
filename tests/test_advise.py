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
        self.assertAlmostEqual(self.uniform_rep["plan"]["quota"]["days_now"], c["days"], places=6)
        self.assertEqual(self.uniform_rep["quota_inferred"]["partial"], True)
        md = advise.memo_md(self.uniform_rep, "en")
        self.assertIn("partial cycle", md)
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


if __name__ == "__main__":
    unittest.main()
