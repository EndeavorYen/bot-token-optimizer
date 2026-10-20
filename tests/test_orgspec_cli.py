"""Organization description (validation, org.txt, init wizard) and the bto command line."""
import contextlib
import io
import json
import os
import tempfile
import unittest

from _paths import ROOT
import cli
import orgopt
import orgspec

SAMPLES = os.path.join(ROOT, "samples")


class TestValidation(unittest.TestCase):
    def test_sample_is_valid(self):
        with open(os.path.join(SAMPLES, "org_before.json"), encoding="utf-8") as f:
            self.assertEqual(orgspec.validate_org(json.load(f)), ([], []))

    def test_collects_every_problem(self):
        org = {"bots": [{"name": "a", "context_tokens": -5, "cache_hit_ratio": 2},
                        {"name": "a", "context_token": 10},
                        {"context_tokens": 1}],
               "links": [{"from": "a", "to": "ghost", "messages_per_day": "lots"}],
               "groups": [{"name": "g", "members": [], "messages_per_day": 1, "avg_tokens": 1, "wake": "some"}]}
        errs, warns = orgspec.validate_org(org)
        text = "\n".join(errs)
        for needle in ["context_tokens", "cache_hit_ratio", "'a' is used more than once", "missing required field 'name'",
                       "'ghost'", "'avg_tokens'", "'messages_per_day' must be a number", "'members'", "'wake'"]:
            self.assertIn(needle, text)
        self.assertTrue(any("context_token" in w and "typo" in w for w in warns))
        with self.assertRaises(orgspec.OrgSpecError):
            orgspec.check_org(org)

    def test_chinese_messages(self):
        errs, _ = orgspec.validate_org({"bots": []}, "zh-TW")
        self.assertIn("非空", errs[0])


class TestTextFormat(unittest.TestCase):
    def test_txt_matches_json_sample(self):
        for name in ("org_before", "org_after"):
            with open(os.path.join(SAMPLES, f"{name}.json"), encoding="utf-8") as f:
                js = json.load(f)
            txt, _ = orgspec.load_org(os.path.join(SAMPLES, f"{name}.txt"))
            self.assertEqual(orgopt.evaluate_org(txt)["total_eq"], orgopt.evaluate_org(js)["total_eq"], name)

    def test_round_trip(self):
        text = ("settings horizon=14 soft_cap=120k\n"
                "bot chief 100k base=8k wakes=3 rounds=1 reasoning=500\n"
                "bot w 1.5k board=4k cache=0.6\n"
                "chief -> w : 6/day 800   # comment\n"
                "group g : chief, w : 10/day 90 wake=all\n")
        org = orgspec.parse_txt(text)
        self.assertEqual(org["horizon_days"], 14)
        self.assertEqual(org["bots"][1], {"name": "w", "context_tokens": 1500, "reads_board_via_tool": True,
                                          "board_read_tokens": 4000, "cache_hit_ratio": 0.6})
        self.assertEqual(org["groups"][0]["members"], ["chief", "w"])
        self.assertEqual(orgspec.parse_txt(orgspec.to_txt(org)), org)

    def test_errors_name_the_line(self):
        with self.assertRaisesRegex(orgspec.OrgSpecError, "line 2"):
            orgspec.parse_txt("bot a 10k\nthis is not valid\n")
        with self.assertRaisesRegex(orgspec.OrgSpecError, "unknown option 'colour=red'"):
            orgspec.parse_txt("bot a 10k colour=red\n")


class TestInitWizard(unittest.TestCase):
    def test_wizard_writes_valid_files(self):
        answers = iter(["chief worker",          # bots
                        "100k", "", "", "n",     # chief: context, wakes (default 3), rounds, board
                        "20k", "", "2", "y",     # worker
                        "chief worker 6 800", "worker chief 6 15", "",   # links
                        "team chief,worker 4 100", ""])                  # groups
        out = os.path.join(tempfile.mkdtemp(), "org")
        org = orgspec.run_init(out, "en", ask=lambda _: next(answers), say=lambda *_: None)
        self.assertEqual(org["bots"][0]["own_wakes_per_day"], 3)
        self.assertTrue(org["bots"][1]["reads_board_via_tool"])
        self.assertEqual(len(org["links"]), 2)
        loaded, _ = orgspec.load_org(out + ".txt")
        self.assertEqual(orgopt.evaluate_org(loaded)["total_eq"], orgopt.evaluate_org(org)["total_eq"])

    def test_wizard_retries_bad_lines(self):
        answers = iter(["a b", "10k", "", "", "n", "10k", "", "", "n",
                        "a ghost 1 1", "a b 1 1", "", ""])
        said = []
        out = os.path.join(tempfile.mkdtemp(), "org")
        org = orgspec.run_init(out, "en", ask=lambda _: next(answers), say=said.append)
        self.assertEqual(len(org["links"]), 1)
        self.assertTrue(any("retry" in s for s in said))


class TestCLI(unittest.TestCase):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_global_options_anywhere(self):
        a = self.run_cli("--lang", "zh-TW", "org", os.path.join(SAMPLES, "org_before.txt"))
        b = self.run_cli("org", os.path.join(SAMPLES, "org_before.txt"), "--lang=zh-TW")
        self.assertEqual(a, b)
        self.assertIn("診斷與建議", a[1])

    def test_platform_changes_span_cap(self):
        _, out, _ = self.run_cli("span", "--n", "50", "--c0", "30000", "--m", "200", "--platform", "grok-bot")
        self.assertEqual(json.loads(out)["k_cap"], 6)

    def test_bad_input_is_a_clean_error(self):
        p = os.path.join(tempfile.mkdtemp(), "bad.json")
        with open(p, "w") as f:
            json.dump({"bots": [{"name": "a"}]}, f)
        code, _, err = self.run_cli("org", p, "--lang", "en")
        self.assertEqual(code, 2)
        self.assertIn("missing required field 'context_tokens'", err)

    def test_convert_and_presets(self):
        tmp = tempfile.mkdtemp()
        src = os.path.join(tmp, "o.json")
        with open(os.path.join(SAMPLES, "org_after.json"), encoding="utf-8") as f, open(src, "w") as g:
            g.write(f.read())
        code, out, _ = self.run_cli("convert", src)
        self.assertEqual(code, 0)
        self.assertTrue(out.strip().endswith("o.txt"))
        _, out, _ = self.run_cli("presets")
        self.assertIn("claude-sonnet-5-5", json.loads(out)["pricing"])

    def test_analyze_writes_skeleton(self):
        tmp = tempfile.mkdtemp()
        sk = os.path.join(tmp, "org.json")
        code, out, _ = self.run_cli("analyze", os.path.join(SAMPLES, "sample_usage.csv"), "--org-out", sk, "--lang", "en")
        self.assertEqual(code, 0)
        self.assertIn("Model calibration", out)
        orgspec.load_org(sk)


if __name__ == "__main__":
    unittest.main()
