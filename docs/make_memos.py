#!/usr/bin/env python3
"""Regenerate the sample memos in docs/ from the synthetic data (CI checks they are current)."""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = [sys.executable, os.path.join(ROOT, "core", "cli.py")]
sys.path.insert(0, os.path.join(ROOT, "samples"))
import make_grok_export  # noqa: E402

grok = make_grok_export.generate(os.path.join(tempfile.mkdtemp(), "usage-events.csv"))
for lang in ("en", "zh-TW"):
    subprocess.run(CLI + ["report", os.path.join(ROOT, "samples", "sample_usage.csv"), "--platform", "grok-bot",
                          "--goal-usd-week", "18", "--lang", lang,
                          "--out", os.path.join(ROOT, "docs", f"sample-memo.{lang}.md")],
                   check=True, stdout=subprocess.DEVNULL)
    subprocess.run(CLI + ["report", grok, "--platform", "grok-bot", "--tz", "+08:00", "--quota-days", "3",
                          "--schedules", os.path.join(ROOT, "samples", "grok_schedules.txt"), "--lang", lang,
                          "--out", os.path.join(ROOT, "docs", f"sample-memo-grok.{lang}.md")],
                   check=True, stdout=subprocess.DEVNULL)
print("docs/sample-memo*.md regenerated")
