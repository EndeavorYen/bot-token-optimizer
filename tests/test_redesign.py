"""Organization redesign: redundant bots, merges, splits, leaders, and the hand-over policy."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from _paths import ROOT  # noqa: F401
import cli
import make_grok_export
import orgopt
import orgspec
import redesign

PRICING = orgopt.load_pricing("grok-4.7")
BEFORE = os.path.join(ROOT, "samples", "org_before.txt")


def load(name):
    return orgspec.load_org(os.path.join(ROOT, "samples", name))[0]


SPAN_TXT = "settings horizon=7\nbot chief 90k base=8k wakes=4 rounds=1 keep\n" + "".join(
    f"bot w{i} 20k rounds=3 wakes=8\n" for i in range(1, 9)) + "".join(
    f"w{i} -> chief : 8/day 1.2k\n" for i in range(1, 9))


class TestHandoverPolicy(unittest.TestCase):
    def test_default_unchanged_and_policy_resets(self):
        org = load("org_before.txt")
        plain = orgopt.evaluate_org(org, PRICING)
        self.assertTrue(all(b["resets"] == 0 for b in plain["bots"]))
        pol = orgopt.evaluate_org(dict(org, handover_at=60000), PRICING)
        self.assertLess(pol["total_eq"], plain["total_eq"])
        res = {b["bot"]: b for b in pol["bots"]}
        self.assertGreater(res["researcher"]["resets"], 0)
        self.assertLessEqual(res["researcher"]["C_end"], 60000)

    def test_txt_round_trip(self):
        org = orgspec.parse_txt("settings handover_at=80k\nbot a 30k keep\nbot b 20k\na -> b : 2/day 100\n")
        self.assertEqual(org["handover_at"], 80000)
        self.assertTrue(org["bots"][0]["keep"])
        again = orgspec.parse_txt(orgspec.to_txt(org))
        self.assertEqual(again, org)
        self.assertEqual(orgspec.check_org(org), [])


class TestRedesign(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.org = load("org_before.txt")
        cls.res = redesign.optimize(cls.org, PRICING, 6)

    def test_finds_redundant_relay_and_merges(self):
        kinds = [(s["kind"], s.get("bot")) for s in self.res["structure"]["steps"]]
        self.assertIn(("retire", "lead"), kinds)                       # a pure relay
        self.assertIn(("merge", "writer"), kinds)                      # a sequential pipeline
        self.assertIn("lead", self.res["removed"])
        names = {b["name"] for b in self.res["structure"]["org"]["bots"]}
        self.assertNotIn("lead", names)
        orgspec.check_org(self.res["structure"]["org"])

    def test_savings_add_up(self):
        steps = self.res["instructions"]["steps"] + self.res["structure"]["steps"]
        self.assertAlmostEqual(self.res["current"]["usd_week"] - self.res["structure"]["usd_week"],
                               sum(s["usd_week"] for s in steps), places=6)
        for s in steps:
            self.assertGreater(s["saving_eq"], 0)
        self.assertLess(self.res["structure"]["usd_week"], self.res["instructions"]["usd_week"])

    def test_keep_is_respected(self):
        org = load("org_before.txt")
        for b in org["bots"]:
            if b["name"] in ("lead", "writer", "reviewer"):
                b["keep"] = True
        res = redesign.optimize(org, PRICING, 6)
        for name in ("lead", "writer", "reviewer"):
            self.assertNotIn(name, res["removed"])

    def test_policy_and_steady_state(self):
        self.assertEqual(self.res["policy"], 120000)                   # the org's soft cap
        base = self.res["current"]["org"]
        self.assertEqual(base["horizon_days"], redesign.EVAL_DAYS)
        self.assertTrue(all(b["context_tokens"] <= 120000 for b in base["bots"]))

    def test_leaders_for_a_wide_span(self):
        org = orgspec.parse_txt(SPAN_TXT)
        res = redesign.optimize(org, PRICING, 6)
        steps = res["structure"]["steps"]
        self.assertEqual(steps[0]["kind"], "leaders")
        sources = {ln["from"] for ln in res["structure"]["org"]["links"] if ln["to"] == "chief"}
        self.assertLessEqual(len(sources), 6)
        self.assertNotIn("chief", res["removed"])

    def test_split_partitions_sources(self):
        org = orgspec.parse_txt("bot hub 80k rounds=2\nbot a 10k\nbot b 10k\nbot c 10k\n"
                                "a -> hub : 6/day 2k\nb -> hub : 6/day 2k\nc -> hub : 6/day 1k\n"
                                "hub -> a : 6/day 300\n")
        o, mv = redesign.split(org, "hub")
        self.assertEqual(sorted(mv["a"] + mv["b"]), ["a", "b", "c"])
        to_twin = {ln["from"] for ln in o["links"] if ln["to"] == "hub-2"}
        self.assertEqual(to_twin - {"hub"}, set(mv["b"]))
        self.assertTrue(any(ln["from"] == "hub" and ln["to"] == "hub-2" for ln in o["links"]))
        self.assertIsNone(redesign.split(orgspec.parse_txt("bot x 10k\nbot y 10k\ny -> x : 1/day 10\n"), "x"))

    def test_merge_keeps_forwarded_content(self):
        org = orgspec.parse_txt("bot up 20k\nbot fwd 20k rounds=0 out=100\nbot work 30k rounds=2\n"
                                "up -> fwd : 4/day 500\nfwd -> work : 4/day 2600\n")
        o, mv = redesign.merge(org, "work", "fwd", 200000)
        ln = next(x for x in o["links"] if x["from"] == "up")
        self.assertEqual(ln["to"], "work")
        self.assertAlmostEqual(ln["avg_tokens"], 500 + (2600 - 100))
        self.assertEqual(redesign._bot(o, "work")["base_tokens"], 8000 + 8000)

    def test_memo(self):
        for lang, head in (("en", "Organization redesign"), ("zh-TW", "組織改造")):
            md = redesign.redesign_md(self.res, PRICING, lang)
            self.assertIn(head, md)
            self.assertEqual(md.count("```mermaid"), 2)
            self.assertIn("lead", md)
            self.assertIn("```text", md)


class TestCli(unittest.TestCase):
    def test_redesign_command(self):
        out = os.path.join(tempfile.mkdtemp(), "after.txt")
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(cli.main(["redesign", BEFORE, "--platform", "grok-bot", "--keep", "writer",
                                       "--out", out]), 0)
        org = orgspec.load_org(out)[0]
        self.assertIn("writer", {b["name"] for b in org["bots"]})
        self.assertNotIn("lead", {b["name"] for b in org["bots"]})

    def test_report_org_skeleton(self):
        d = tempfile.mkdtemp()
        csv_path = make_grok_export.generate(os.path.join(d, "g.csv"))
        sk = os.path.join(d, "org.json")
        with redirect_stdout(io.StringIO()):
            cli.main(["report", csv_path, "--schedules", os.path.join(ROOT, "samples", "grok_schedules.txt"),
                      "--tz", "+08:00", "--org-out", sk])
        with open(sk, encoding="utf-8") as f:
            org = json.load(f)
        names = [b["name"] for b in org["bots"]]
        self.assertIn("monitor", names)
        self.assertTrue(all(" " not in n for n in names))
        orgspec.check_org(org)


if __name__ == "__main__":
    unittest.main()
