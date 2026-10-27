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


def _bot_empty(report):
    return next(gap for gap in report["gaps"] if gap["code"] == "bot_empty")


def _noisy_webhook_export(directory):
    """50 random wake-ups over 7 days and one webhook line every 30 seconds."""
    rng = random.Random(1)
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
    return csv_path, events_path


def _ci_run_with_dense_log(directory):
    """Six wakes 2 s after ci-run, four closer to a 30 s webhook grid.

    Same shape as the ci-run fixture in tests/test_events.py: ci-run stays
    reliable and the dense log does not.
    """
    start = datetime(2026, 10, 12, tzinfo=timezone.utc)
    rows = ["timestamp,automation_id,model,input_tokens,cached_tokens,output_tokens"]
    events = []
    for hour in range(6):
        ev = start + timedelta(hours=hour, seconds=10)
        wake = ev + timedelta(seconds=2)
        events.append((ev, "ci-run"))
        rows.append(f"{_ts(wake)},,demo-model,20000,0,100")
    for hour in range(6, 10):
        ev = start + timedelta(hours=hour, seconds=10)
        wake = ev + timedelta(seconds=25)
        events.append((ev, "ci-run"))
        rows.append(f"{_ts(wake)},,demo-model,20000,0,100")
    csv_path = os.path.join(directory, "usage.csv")
    events_path = os.path.join(directory, "events.tsv")
    with open(csv_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(rows) + "\n")
    end = start + timedelta(hours=9, seconds=35)
    with open(events_path, "w", encoding="utf-8") as handle:
        for ev, source in events:
            handle.write(f"{_ts(ev)}\t{source}\n")
        moment = start
        while moment <= end:
            handle.write(f"{_ts(moment)}\tnoisy-webhook\n")
            moment += timedelta(seconds=30)
    return csv_path, events_path


class TestBotEmptyEventDetail(unittest.TestCase):
    def test_noisy_webhook_omits_event_attribution_sentence(self):
        directory = tempfile.mkdtemp()
        csv_path, events_path = _noisy_webhook_export(directory)
        loaded = advise.load_events(events_path)
        for lang, marker in (("en", "attribute"), ("zh-TW", "事件紀錄")):
            report = advise.report(csv_path, PRICING, events=loaded, lang=lang)
            detail = _bot_empty(report)["detail"]
            self.assertNotIn(marker, detail)
            self.assertEqual(_gap_severity(report, "bot_empty"), "high")
            self.assertEqual(report["plan"]["confidence"], "low")

    def test_reliable_ci_run_keeps_its_share_and_count(self):
        directory = tempfile.mkdtemp()
        csv_path, events_path = _ci_run_with_dense_log(directory)
        loaded = advise.load_events(events_path)
        en = advise.report(csv_path, PRICING, events=loaded, lang="en")
        zh = advise.report(csv_path, PRICING, events=loaded, lang="zh-TW")
        reliable = [row for row in en["attribution"]["events"] if row["reliable"]]
        self.assertEqual([row["name"] for row in reliable], ["ci-run"])
        counted = sum(1 for row in reliable if row["stats"])
        self.assertEqual(counted, 1)
        share = sum(row["share"] for row in reliable)
        en_sentence = advise.t("g.att_events", "en", p=share, n=counted)
        zh_sentence = advise.t("g.att_events", "zh-TW", p=share, n=counted)
        self.assertIn(en_sentence, _bot_empty(en)["detail"])
        self.assertIn(zh_sentence, _bot_empty(zh)["detail"])
        self.assertNotIn("2 event sources", _bot_empty(en)["detail"])
