"""Event-triggered wake-ups belong to their event source; the rest stays unattributed.

Synthetic rows only: one scheduled bot, one event-triggered routine, and interactive
wake-ups that match neither. No pause or frequency change is recommended for the
unattributed remainder.
"""
import io
import os
import random
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

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

    def test_source_names_are_kept_exactly(self):
        sources = ["trailing space ", " leading space", "中文名", "a\tb", "emoji 🚀"]
        lines = ["# comment", ""]
        rows = ["timestamp,automation_id,model,input_tokens,cached_tokens,output_tokens"]
        for n, source in enumerate(sources):
            lines.append(f"2026-10-12T{n:02d}:00:00Z\t{source}")
            wake = datetime(2026, 10, 12, n, 0, 2, tzinfo=timezone.utc)
            rows.append(f"{wake.strftime('%Y-%m-%dT%H:%M:%SZ')},,demo-model,20000,0,100")
        directory = tempfile.mkdtemp()
        events_path = os.path.join(directory, "events.tsv")
        csv_path = os.path.join(directory, "usage.csv")
        with open(events_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")
        events = advise.load_events(events_path)
        self.assertEqual([ev["source"] for ev in events], sources)
        rep = advise.report(csv_path, PRICING, events=events)
        self.assertEqual([ev["name"] for ev in rep["attribution"]["events"]], sources)
        self.assertTrue(all(ev["matched"] == 1 for ev in rep["attribution"]["events"]))

    def test_whitespace_source_names_the_line(self):
        text = "\n".join([
            "2026-10-12T00:00:00Z\tok",
            "2026-10-12T01:00:00Z\t   ",
            "2026-10-12T02:00:00Z\t",
        ])
        with self.assertRaises(advise.EventError) as cm:
            advise.parse_events(text)
        msg = str(cm.exception)
        self.assertIn("line 2", msg)
        self.assertIn("line 3", msg)
        self.assertNotIn("line 1", msg)

    def test_tie_uses_the_schedule_name_order(self):
        """Same distance from a daily fire and an event: the name that sorts first wins.

        Six fires, so an event that wins every tie is reliable and keeps the wake-ups.
        One fire is not enough: the event fails the chance check and the schedule matches again.
        """
        def assigned(event_name):
            directory = tempfile.mkdtemp()
            csv_path = os.path.join(directory, "usage.csv")
            sched_path = os.path.join(directory, "schedules.txt")
            events_path = os.path.join(directory, "events.tsv")
            with open(csv_path, "w", encoding="utf-8") as f:
                f.write("timestamp,automation_id,model,input_tokens,cached_tokens,output_tokens\n")
                for day in range(12, 18):
                    f.write(f"2026-10-{day:02d}T00:00:10Z,,demo-model,20000,0,100\n")
            with open(sched_path, "w", encoding="utf-8") as f:
                f.write("hourly at 00:00 in=demo-model\n")
            with open(events_path, "w", encoding="utf-8") as f:
                for day in range(12, 18):
                    f.write(f"2026-10-{day:02d}T00:00:00Z\t{event_name}\n")
            rep = advise.report(csv_path, PRICING, schedule_specs=schedules.load(sched_path),
                                events=advise.load_events(events_path))
            att = rep["attribution"]
            hourly = next(b for b in att["bots"] if b["name"] == "hourly")
            event = next(e for e in att["events"] if e["name"] == event_name)
            return hourly["matched"], event["matched"], event["reliable"]

        self.assertEqual(assigned("zzz-event"), (6, 0, False))
        self.assertEqual(assigned("aaa-event"), (0, 6, True))

    def test_reliable_source_reclaims_wakes_a_dense_log_took(self):
        """A source that stays reliable gets the wake-ups a rejected log had taken."""
        start = datetime(2026, 10, 12, tzinfo=timezone.utc)
        rows = ["timestamp,automation_id,model,input_tokens,cached_tokens,output_tokens"]
        events = []
        # Six wakes sit next to ci-run. The dense log is farther away, so ci-run keeps them
        # and stays reliable. Four more sit closer to a 30 s grid point than to ci-run.
        for hour in range(6):
            ev = start + timedelta(hours=hour, seconds=10)
            wake = ev + timedelta(seconds=2)
            events.append((ev, "ci-run"))
            rows.append(f"{wake.strftime('%Y-%m-%dT%H:%M:%SZ')},,demo-model,20000,0,100")
        for hour in range(6, 10):
            ev = start + timedelta(hours=hour, seconds=10)
            wake = ev + timedelta(seconds=25)
            events.append((ev, "ci-run"))
            rows.append(f"{wake.strftime('%Y-%m-%dT%H:%M:%SZ')},,demo-model,20000,0,100")
        directory = tempfile.mkdtemp()
        csv_path = os.path.join(directory, "usage.csv")
        events_path = os.path.join(directory, "events.tsv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")
        end = start + timedelta(hours=9, seconds=35)
        with open(events_path, "w", encoding="utf-8") as f:
            for ev, source in events:
                f.write(f"{ev.strftime('%Y-%m-%dT%H:%M:%SZ')}\t{source}\n")
            t = start
            while t <= end:
                f.write(f"{t.strftime('%Y-%m-%dT%H:%M:%SZ')}\tnoisy-webhook\n")
                t += timedelta(seconds=30)
        rep = advise.report(csv_path, PRICING, events=advise.load_events(events_path))
        att = rep["attribution"]
        ci = next(e for e in att["events"] if e["name"] == "ci-run")
        noisy = next(e for e in att["events"] if e["name"] == "noisy-webhook")
        self.assertEqual(ci["matched"], 10)
        self.assertTrue(ci["reliable"])
        self.assertFalse(noisy["reliable"])
        self.assertIsNone(noisy["stats"])
        self.assertEqual(sum(r["stats"]["wakes"] for r in att["remainder"]), 0)

    def test_issue5_paste_lists_bots_not_event_sources(self):
        """Issue #5 repro: 7 days, one hourly bot, 70 interactive wakes, 30 overnight chains."""
        start = datetime(2026, 10, 12, tzinfo=timezone.utc)
        rows = ["timestamp,automation_id,model,input_tokens,cached_tokens,output_tokens"]

        def add(ts, inp, out, n=1, gap=8):
            for i in range(n):
                t = ts + timedelta(seconds=gap * i)
                rows.append(f"{t.strftime('%Y-%m-%dT%H:%M:%SZ')},,demo-model,{inp},0,{out}")

        t = start
        while t < start + timedelta(days=7):
            add(t + timedelta(seconds=5), 20000, 40)
            t += timedelta(hours=1)
        for day in range(7):
            for hour in range(8, 18):
                add(start + timedelta(days=day, hours=hour, minutes=15), 30000, 250)
        event_times = []
        for day in range(6):
            for hour in range(5):
                ev = start + timedelta(days=day, hours=hour, minutes=30)
                event_times.append(ev)
                add(ev + timedelta(seconds=2), 49000, 400, n=2)
        directory = tempfile.mkdtemp()
        csv_path = os.path.join(directory, "usage.csv")
        sched_path = os.path.join(directory, "schedules.txt")
        events_path = os.path.join(directory, "events.tsv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")
        with open(sched_path, "w", encoding="utf-8") as f:
            f.write("hourly every 60m in=demo-model\n")
        with open(events_path, "w", encoding="utf-8") as f:
            for ev in event_times:
                f.write(f"{ev.strftime('%Y-%m-%dT%H:%M:%SZ')}\tci-run-created\n")
        rep = advise.report(csv_path, PRICING, schedule_specs=schedules.load(sched_path),
                            events=advise.load_events(events_path))
        matched = next(e for e in rep["attribution"]["events"] if e["name"] == "ci-run-created")
        self.assertEqual((matched["matched"], matched["fires"]), (30, 30))
        md = advise.memo_md(rep, "en")
        paste = md.split("### Paste to the main bot", 1)[1].split("\n## ", 1)[0]
        self.assertIn("hourly", paste)
        self.assertNotIn("ci-run-created", paste)

    def test_dense_log_is_unreliable_and_not_a_cut_target(self):
        """300 random wake-ups and one source logged every 30 s.

        Chance uses every wake-up eligible before matching, so the source is not
        reliable. Its wake-ups stay unattributed: the memo has no hotspot and does
        not name the source in a wake-less-often, decide-hot, or goal-cut line.
        """
        rng = random.Random(11)
        start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        slot = 121
        offsets = sorted(rng.sample(range(0, 7 * 24 * 3600 - slot, slot), 300))
        rows = ["timestamp,automation_id,model,input_tokens,cached_tokens,output_tokens"]
        for off in offsets:
            t = start + timedelta(seconds=off)
            rows.append(f"{t.strftime('%Y-%m-%dT%H:%M:%SZ')},,demo-model,20000,0,200")
        directory = tempfile.mkdtemp()
        csv_path = os.path.join(directory, "usage.csv")
        events_path = os.path.join(directory, "events.tsv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")
        end = start + timedelta(seconds=offsets[-1])
        with open(events_path, "w", encoding="utf-8") as f:
            t = start
            while t <= end:
                f.write(f"{t.strftime('%Y-%m-%dT%H:%M:%SZ')}\tnoisy-webhook\n")
                t += timedelta(seconds=30)
        events = advise.load_events(events_path)
        banned = {
            "en": (
                "Wake noisy-webhook less often",
                "Decide whether noisy-webhook's wake-up frequency",
                "fewer wake-ups of noisy-webhook",
            ),
            "zh-TW": (
                "減少 noisy-webhook 的喚醒次數",
                "決定 noisy-webhook 的喚醒頻率",
                "減少 noisy-webhook 的喚醒",
                "noisy-webhook 的喚醒再減少",
            ),
        }
        for lang, phrases in banned.items():
            rep = advise.report(csv_path, PRICING, events=events, goal_usd_week=1, lang=lang)
            ev = next(e for e in rep["attribution"]["events"] if e["name"] == "noisy-webhook")
            self.assertFalse(ev["reliable"])
            self.assertIsNone(ev["stats"])
            self.assertEqual(sum(r["stats"]["wakes"] for r in rep["attribution"]["remainder"]), 300)
            self.assertNotIn("hotspot", {a["code"] for a in rep["plan"]["actions"]})
            self.assertNotEqual((rep["plan"]["goal"] or {}).get("hotspot_unit"), "noisy-webhook")
            md = advise.memo_md(rep, lang)
            for phrase in phrases:
                self.assertNotIn(phrase, md)

    def test_rejected_dense_log_returns_wakes_to_the_schedule(self):
        """Hourly wake-ups 45 s after each fire, and one source logged every 30 s.

        The dense log is not reliable. Matching again without it gives those
        wake-ups back to the schedule, which stays reliable. The memo does not
        name the source in a hotspot, decide-hot, or goal-cut line.
        """
        start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        rows = ["timestamp,automation_id,model,input_tokens,cached_tokens,output_tokens"]
        for i in range(7 * 24):
            t = start + timedelta(hours=i, seconds=45)
            rows.append(f"{t.strftime('%Y-%m-%dT%H:%M:%SZ')},,demo-model,20000,0,100")
        directory = tempfile.mkdtemp()
        csv_path = os.path.join(directory, "usage.csv")
        sched_path = os.path.join(directory, "schedules.txt")
        events_path = os.path.join(directory, "events.tsv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")
        with open(sched_path, "w", encoding="utf-8") as f:
            f.write("hourly every 60m in=demo-model\n")
        end = start + timedelta(hours=7 * 24 - 1, seconds=45)
        with open(events_path, "w", encoding="utf-8") as f:
            t = start
            while t <= end:
                f.write(f"{t.strftime('%Y-%m-%dT%H:%M:%SZ')}\twebhook\n")
                t += timedelta(seconds=30)
        specs = schedules.load(sched_path)
        bare = advise.report(csv_path, PRICING, schedule_specs=specs)
        bare_bot = next(b for b in bare["attribution"]["bots"] if b["name"] == "hourly")
        self.assertEqual(bare_bot["fires"], 168)
        self.assertGreaterEqual(bare_bot["matched"], 167)
        self.assertTrue(bare_bot["reliable"])
        events = advise.load_events(events_path)
        banned = {
            "en": (
                "Wake webhook less often",
                "Decide whether webhook's wake-up frequency",
                "fewer wake-ups of webhook",
            ),
            "zh-TW": (
                "減少 webhook 的喚醒次數",
                "決定 webhook 的喚醒頻率",
                "減少 webhook 的喚醒",
                "webhook 的喚醒再減少",
            ),
        }
        for lang, phrases in banned.items():
            rep = advise.report(csv_path, PRICING, schedule_specs=specs, events=events,
                                goal_usd_week=1, lang=lang)
            att = rep["attribution"]
            hourly = next(b for b in att["bots"] if b["name"] == "hourly")
            self.assertEqual(hourly["fires"], 168)
            self.assertGreaterEqual(hourly["matched"], 167)
            self.assertTrue(hourly["reliable"])
            ev = next(e for e in att["events"] if e["name"] == "webhook")
            self.assertFalse(ev["reliable"])
            self.assertIsNone(ev["stats"])
            self.assertNotEqual((rep["plan"]["goal"] or {}).get("hotspot_unit"), "webhook")
            for action in rep["plan"]["actions"]:
                if action["code"] == "hotspot":
                    self.assertNotEqual(action["unit"], "webhook")
            md = advise.memo_md(rep, lang)
            for phrase in phrases:
                self.assertNotIn(phrase, md)

    def test_sixty_second_log_over_random_traffic_is_not_the_hotspot(self):
        """A weak event every 60 s over random traffic stays unreliable and unnamed as a cut."""
        rng = random.Random(11)
        start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        slot = 121
        offsets = sorted(rng.sample(range(0, 7 * 24 * 3600 - slot, slot), 300))
        rows = ["timestamp,automation_id,model,input_tokens,cached_tokens,output_tokens"]
        for off in offsets:
            t = start + timedelta(seconds=off)
            rows.append(f"{t.strftime('%Y-%m-%dT%H:%M:%SZ')},,demo-model,20000,0,200")
        directory = tempfile.mkdtemp()
        csv_path = os.path.join(directory, "usage.csv")
        events_path = os.path.join(directory, "events.tsv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")
        end = start + timedelta(seconds=offsets[-1])
        with open(events_path, "w", encoding="utf-8") as f:
            t = start
            while t <= end:
                f.write(f"{t.strftime('%Y-%m-%dT%H:%M:%SZ')}\tweak-log\n")
                t += timedelta(seconds=60)
        events = advise.load_events(events_path)
        banned = {
            "en": (
                "Wake weak-log less often",
                "Decide whether weak-log's wake-up frequency",
                "fewer wake-ups of weak-log",
            ),
            "zh-TW": (
                "減少 weak-log 的喚醒次數",
                "決定 weak-log 的喚醒頻率",
                "減少 weak-log 的喚醒",
                "weak-log 的喚醒再減少",
            ),
        }
        for lang, phrases in banned.items():
            rep = advise.report(csv_path, PRICING, events=events, goal_usd_week=1, lang=lang)
            ev = next(e for e in rep["attribution"]["events"] if e["name"] == "weak-log")
            self.assertFalse(ev["reliable"])
            self.assertIsNone(ev["stats"])
            self.assertNotIn("hotspot", {a["code"] for a in rep["plan"]["actions"]})
            self.assertNotEqual((rep["plan"]["goal"] or {}).get("hotspot_unit"), "weak-log")
            md = advise.memo_md(rep, lang)
            for phrase in phrases:
                self.assertNotIn(phrase, md)
