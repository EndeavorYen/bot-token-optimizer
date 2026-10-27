"""Event-triggered wake-ups belong to their event source; the rest stays unattributed.

Synthetic rows only: one scheduled bot, one event-triggered routine, and interactive
wake-ups that match neither. No pause or frequency change is recommended for the
unattributed remainder.
"""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta

from _paths import ROOT
import advise
import cli
import orgopt
import schedules

PRICING = orgopt.load_pricing("grok-4.7")
OLD_EXCLUSIVE = "your messages and bot-to-bot pings"
UNATTRIBUTED = "unattributed traffic (your messages, bot-to-bot pings, and event-triggered automations)"


def _write_fixture(directory):
    """Eight scheduled wakes, six two-call event chains, four interactive wakes."""
    rows = ["timestamp,automation_id,model,input_tokens,cached_tokens,output_tokens"]

    def add(ts, inp, out, n=1, gap=10):
        base = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        for i in range(n):
            t = base + timedelta(seconds=gap * i)
            rows.append(f"{t.strftime('%Y-%m-%dT%H:%M:%SZ')},,demo-model,{inp},0,{out}")

    for hour in range(8):
        add(f"2026-10-12T{hour:02d}:00:05Z", 20000, 100)
    event_times = []
    for hour in range(6):
        event_times.append(f"2026-10-12T{hour:02d}:30:00Z")
        add(f"2026-10-12T{hour:02d}:30:02Z", 49000, 400, n=2, gap=8)
    for hour in range(4):
        add(f"2026-10-12T{hour:02d}:15:00Z", 30000, 250)

    csv_path = os.path.join(directory, "usage.csv")
    sched_path = os.path.join(directory, "schedules.txt")
    events_path = os.path.join(directory, "events.tsv")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")
    with open(sched_path, "w", encoding="utf-8") as f:
        f.write("hourly every 60m in=demo-model\n")
    with open(events_path, "w", encoding="utf-8") as f:
        for ts in event_times:
            f.write(f"{ts}\treviewer-pr-listener\n")
    return csv_path, sched_path, events_path


class TestUnscheduledTrafficOwner(unittest.TestCase):
    def setUp(self):
        self.csv, self.sched, self.events = _write_fixture(tempfile.mkdtemp())
        self.specs = schedules.load(self.sched)

    def _pause_requests(self, actions):
        for action in actions:
            request = action.get("request") or ""
            if isinstance(request, list):
                request = " ".join(request)
            if "pause" in request.lower() or action["code"] == "frequency":
                return action
        return None

    def test_without_events_remainder_stays_unattributed(self):
        rep = advise.report(self.csv, PRICING, schedule_specs=self.specs)
        md = advise.memo_md(rep, "en")
        remainder = rep["attribution"]["remainder"]
        self.assertEqual(sum(r["stats"]["wakes"] for r in remainder), 10)
        self.assertIn(UNATTRIBUTED, md)
        self.assertNotIn(OLD_EXCLUSIVE, md)
        self.assertIsNone(self._pause_requests(rep["plan"]["actions"]))
        confirm = [a for a in rep["plan"]["actions"] if a["code"] == "confirm_owner"]
        self.assertEqual(len(confirm), 1)
        self.assertEqual(confirm[0]["tier"], "judgment")
        self.assertEqual(confirm[0]["usd_week_high"], 0.0)
        self.assertIn("confirm owner and trigger first", confirm[0]["how"].lower())
        self.assertNotIn("pause", (confirm[0].get("request") or "").lower())
        tight = advise.memo_md(advise.report(self.csv, PRICING, schedule_specs=self.specs, goal_usd_week=1), "en")
        self.assertNotRegex(tight, r"fewer wake-ups of .*unattributed")

    def test_events_attribute_chains_to_source(self):
        events = advise.load_events(self.events)
        rep = advise.report(self.csv, PRICING, schedule_specs=self.specs, events=events)
        att = rep["attribution"]
        hourly = next(b for b in att["bots"] if b["name"] == "hourly")
        self.assertEqual(hourly["matched"], 8)
        matched = next(e for e in att["events"] if e["name"] == "reviewer-pr-listener")
        self.assertEqual((matched["matched"], matched["fires"]), (6, 6))
        self.assertTrue(matched["reliable"])
        self.assertEqual(sum(r["stats"]["wakes"] for r in att["remainder"]), 4)
        total = (sum(b["stats"]["usd"] for b in att["bots"])
                 + sum(e["stats"]["usd"] for e in att["events"] if e["stats"])
                 + sum(r["stats"]["usd"] for r in att["remainder"]))
        self.assertAlmostEqual(total, rep["analysis"]["total_usd"], places=6)
        md = advise.memo_md(rep, "en")
        self.assertIn("reviewer-pr-listener", md)
        self.assertIn(UNATTRIBUTED, md)
        self.assertNotIn(OLD_EXCLUSIVE, md)
        self.assertIsNone(self._pause_requests(rep["plan"]["actions"]))

    def test_cli_events_flag(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["report", self.csv, "--schedules", self.sched, "--events", self.events])
        self.assertEqual(rc, 0)
        text = buf.getvalue()
        self.assertIn("reviewer-pr-listener", text)
        self.assertIn(UNATTRIBUTED, text)
        self.assertNotIn(OLD_EXCLUSIVE, text)

    def test_playbook_requires_confirming_owner_before_pause(self):
        path = os.path.join(ROOT, "adapters", "grok-bot", "ADVISOR_BOT.en.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Before proposing to pause any automation", text)
        self.assertIn("confirm the owner and the trigger first", text.lower())
