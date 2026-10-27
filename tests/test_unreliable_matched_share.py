"""A noisy, unreliable event log must not lower the empty-bot gap or plan confidence.

Fifty random wake-ups over seven days have no scheduled bot. A webhook logged every
30 seconds is not a reliable owner of that traffic. Counting its share used to drop
bot_empty from high to medium and raise plan confidence.
"""
import os
import random
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

_TESTS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_TESTS)
for _path in (_TESTS, os.path.join(_ROOT, "core")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import advise
import orgopt

PRICING = orgopt.load_pricing("grok-4.7")
START = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _ts(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _gap_severity(report, code):
    return next(gap["severity"] for gap in report["gaps"] if gap["code"] == code)


class TestUnreliableMatchedShare(unittest.TestCase):
    def test_noisy_webhook_keeps_bot_empty_high(self):
        rng = random.Random(1)
        directory = tempfile.mkdtemp()
        rows = ["timestamp,automation_id,model,input_tokens,cached_tokens,output_tokens"]
        offsets = sorted(rng.uniform(0, 7 * 86400) for _ in range(50))
        rows.extend(
            f"{_ts(START + timedelta(seconds=offset))},,demo-model,20000,0,200" for offset in offsets)
        csv_path = os.path.join(directory, "u.csv")
        events_path = os.path.join(directory, "e.tsv")
        with open(csv_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(rows) + "\n")
        events = []
        moment = START
        end = START + timedelta(days=7)
        while moment <= end:
            events.append(f"{_ts(moment)}\tnoisy-webhook")
            moment += timedelta(seconds=30)
        with open(events_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(events) + "\n")

        bare = advise.report(csv_path, PRICING, lang="en")
        with_events = advise.report(
            csv_path, PRICING, events=advise.load_events(events_path), lang="en")
        source = next(row for row in with_events["attribution"]["events"] if row["name"] == "noisy-webhook")

        self.assertFalse(source["reliable"])
        self.assertEqual(_gap_severity(bare, "bot_empty"), "high")
        self.assertEqual(_gap_severity(with_events, "bot_empty"), "high")
        self.assertEqual(bare["plan"]["confidence"], "low")
        self.assertEqual(with_events["plan"]["confidence"], bare["plan"]["confidence"])
