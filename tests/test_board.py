"""Boards: how much a read brings in, what a big board costs, and the advice to keep it small."""
import csv
import io
import json
import os
import random
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

from _paths import ROOT  # noqa: F401
import advise
import analyze_usage
import cli
import orgopt
import orgspec
import redesign

PRICING = orgopt.load_pricing("grok-4.7")


def bloated_board_csv(path, days=7, pings=10, board_from=8000, board_to=14000):
    """One bot that, on every ping, first reads a growing board (short first call), then works."""
    t0 = datetime(2026, 10, 12, tzinfo=timezone.utc)
    rows, c = [], 40000.0
    for d in range(days):
        board = board_from + (board_to - board_from) * d / (days - 1)
        for i in range(pings):
            t = t0 + timedelta(days=d, hours=8 + i * 1.3)
            rows.append([t.isoformat(), "planner", "main", int(c), int(c * 0.8), 20])
            c2 = c + 20 + board
            rows.append([(t + timedelta(seconds=30)).isoformat(), "planner", "main", int(c2), int(c * 0.8), 400])
            c = c2 + 700
            if c > 165000:
                c = 50000.0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "bot_name", "chat_id", "input_tokens", "cached_input_tokens", "output_tokens"])
        w.writerows(rows)
    return path


class TestFetchMeasurement(unittest.TestCase):
    def test_fetched_tokens(self):
        wake = [{"input": 50000, "output": 20}, {"input": 62020, "output": 400}]
        self.assertEqual(analyze_usage.fetched_tokens(wake), 12000)
        st = analyze_usage.fetch_stats([[{"input": 0, "output": 0}, {"input": x, "output": 0}]
                                        for x in (1000, 1000, 1000, 2000, 2000, 2000)])
        self.assertEqual((st["fetch_p50"], st["fetch_trend"]), (1500, 2.0))
        self.assertEqual(analyze_usage.fetch_stats([])["fetch_p50"], 0.0)

    def test_measured_from_export(self):
        p = bloated_board_csv(os.path.join(tempfile.mkdtemp(), "b.csv"))
        res = analyze_usage.run(p, PRICING)
        u = res["units"][0]
        self.assertEqual(u["read_first_wakes"], 70)
        self.assertAlmostEqual(u["fetch_p50"], 11000, delta=200)
        self.assertGreater(u["fetch_trend"], advise.BOARD_GROWING)


class TestBoardCost(unittest.TestCase):
    def test_options_order(self):
        o = orgopt.board_options(60000, 10, 12000, 2000, k=1, pricing=PRICING)
        self.assertGreater(o["full_board_usd_week"], o["section_usd_week"])
        self.assertGreater(o["section_usd_week"], o["inline_usd_week"])
        self.assertAlmostEqual(o["saving_section_usd_week"], o["full_board_usd_week"] - o["section_usd_week"])
        # under a hand-over policy the context cannot balloon, so a big board costs less, but still more
        h = orgopt.board_options(60000, 10, 12000, 2000, k=1, handover_at=100000, pricing=PRICING)
        self.assertLess(h["full_board_usd_week"], o["full_board_usd_week"])
        self.assertGreater(h["saving_section_usd_week"], 0)

    def test_cli(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["board", "--k", "1", "--board", "12000", "--section", "2000"])
        out = json.loads(buf.getvalue())
        self.assertIn("weekly_cost", out)
        self.assertAlmostEqual(out["token_ratio"], 1.5)


class TestBoardAdvice(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.csv = bloated_board_csv(os.path.join(tempfile.mkdtemp(), "b.csv"))
        cls.rep = advise.report(cls.csv, PRICING, lang="zh-TW")
        cls.acts = {a["code"]: a for a in cls.rep["plan"]["actions"]}

    def test_board_trim_action(self):
        a = self.acts["board_trim"]
        self.assertEqual(a["tier"], "modeled")
        self.assertAlmostEqual(a["per_unit"][0]["fetch_p50"], 11000, delta=200)
        self.assertEqual(a["per_unit"][0]["section"], advise.BOARD_SECTION)
        self.assertIn("還在變大", a["why"])
        self.assertEqual(len(a["request"]), 2)                     # read only a section + maintain it
        self.assertIn("board-archive", a["request"][1])

    def test_trimming_unlocks_hand_overs(self):
        ho = self.acts["handover"]
        self.assertIn("前提是 board 已精簡", ho["why"])
        e = next(e for e in self.rep["plan"]["entities"] if e["name"] == "planner")
        self.assertTrue(e["board_trimmed"])
        # without the trim, the same context would need more hand-overs than the weekly limit
        fresh = advise.FRESH_BASE + orgopt.DEFAULT_HANDOVER
        untrimmed = e["growth_week"] + (e["fetch_p50"] - advise.BOARD_SECTION) * e["read_first_wakes"] / \
            self.rep["analysis"]["span_days"] * 7
        self.assertGreater(untrimmed / (ho["per_unit"][0]["at"] - fresh), advise.MAX_HANDOVERS_WEEK)

    def test_totals_stay_below_spend(self):
        pl = self.rep["plan"]
        self.assertLessEqual(pl["combined_high"], pl["usd_per_week"])
        md = advise.memo_md(self.rep, "zh-TW")
        self.assertIn("board-archive", md)

    def test_small_board_gives_no_trim(self):
        p = bloated_board_csv(os.path.join(tempfile.mkdtemp(), "s.csv"), board_from=1500, board_to=1500)
        codes = {a["code"] for a in advise.report(p, PRICING)["plan"]["actions"]}
        self.assertNotIn("board_trim", codes)

    def test_board_trim_paste_omits_event_sources(self):
        """A logged event source that reads the board is not named in the paste-in request."""
        directory = tempfile.mkdtemp()
        csv_path = bloated_board_csv(os.path.join(directory, "b.csv"))
        events_path = os.path.join(directory, "events.tsv")
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        with open(events_path, "w", encoding="utf-8") as f:
            for row in rows:
                if row["output_tokens"] != "20":
                    continue
                when = analyze_usage.parse_time(row["timestamp"]) - timedelta(seconds=2)
                f.write(f"{when.strftime('%Y-%m-%dT%H:%M:%SZ')}\tboard-webhook\n")
        titles = {"en": "Keep the board small", "zh-TW": "讓 board 保持精簡"}
        headings = {"en": "### Paste to the main bot", "zh-TW": "### 貼給主 bot 的變更請求"}
        for lang in ("en", "zh-TW"):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["report", csv_path, "--events", events_path, "--lang", lang])
            self.assertEqual(rc, 0)
            md = buf.getvalue()
            self.assertIn(titles[lang], md)
            paste = md.split(headings[lang], 1)[1].split("\n## ", 1)[0]
            self.assertNotIn("board-webhook", paste)
            self.assertIn("board-archive", paste)


class TestRedesignBoard(unittest.TestCase):
    def test_trim_move(self):
        org = orgspec.parse_txt("bot boss 40k wakes=2\nbot worker 50k rounds=2 board=12k\n"
                                "boss -> worker : 10/day 100\n")
        res = redesign.optimize(org, PRICING, 6)
        kinds = [s["kind"] for s in res["instructions"]["steps"]]
        self.assertTrue({"board_trim", "board"} & set(kinds))
        w = redesign._bot(res["instructions"]["org"], "worker")
        # either it reads only a section, or the section travels in the message: never the whole board
        self.assertTrue(not w.get("reads_board_via_tool") or w["board_read_tokens"] <= redesign.BOARD_SECTION)
        msg = next(ln for ln in res["instructions"]["org"]["links"] if ln["to"] == "worker")
        self.assertLessEqual(msg["avg_tokens"], 100 + redesign.BOARD_SECTION)
        self.assertIn("board", redesign.redesign_md(res, PRICING, "en"))


if __name__ == "__main__":
    unittest.main()
