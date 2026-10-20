#!/usr/bin/env python3
"""Generate a synthetic Grok Bot usage-events export (fixed seed, reproducible).

Same columns as the real export: Date, Cloud Agent ID, Automation ID, Kind, Model, Max Mode,
Input (w/ Cache Write), Input (w/o Cache Write), Cache Read, Output Tokens, Total Tokens, Cost.

Planted situation (the common real-world one):
  - Automation ID is empty everywhere, so bots can only be grouped by model
  - grok-bot-default carries most of the spend: several chats interleaved, each auto-summarized
    between ~55K and ~165K tokens (sawtooth), and wake-ups rise in the second half of the week
  - grok-bot-automation: routine checks, many of which only acknowledge
  - a few cloud-agent rows (excluded by the analyzer); Cost is "Included" (plan-billed)
"""
import csv
import random
import sys
from datetime import datetime, timedelta, timezone

HEADER = ["Date", "Cloud Agent ID", "Automation ID", "Kind", "Model", "Max Mode", "Input (w/ Cache Write)",
          "Input (w/o Cache Write)", "Cache Read", "Output Tokens", "Total Tokens", "Cost"]


def _row(t, model, inp, hit, out, cloud=""):
    cached = int(inp * hit)
    write = int((inp - cached) * 0.7)
    unc = inp - cached - write
    return [t.strftime("%Y-%m-%dT%H:%M:%S.000Z"), cloud, "", "Included", model, "No",
            write, unc, cached, out, inp + out, "Included"]


def generate(path, days=7, seed=11):
    rnd = random.Random(seed)
    start = datetime(2026, 10, 12, tzinfo=timezone.utc)
    rows = []
    # interactive / bot-to-bot traffic: 3 chats, auto-summarized at ~165K back to ~55K
    chats = [{"c": 55000 + 30000 * i, "g": 9000 + 3000 * i} for i in range(3)]
    for d in range(days):
        wakes = 33 if d < days / 2 else 47
        for i in range(wakes):
            t = start + timedelta(days=d, hours=7 + 15 * (i + rnd.random() * 0.6) / wakes)
            ch = rnd.choice(chats)
            ch["c"] += ch["g"] / 4 + rnd.uniform(-1500, 1500)
            if ch["c"] > 165000:
                ch["c"] = 55000 + rnd.uniform(-2000, 2000)
            for k in range(rnd.choice([1, 2, 2, 3])):
                inp = int(ch["c"] + 1200 * k)
                hit = 0.15 if k == 0 else 0.85
                rows.append(_row(t + timedelta(seconds=25 * k), "grok-bot-default", inp, hit, rnd.randint(250, 900)))
    # routine automation: 24 short checks a day, 60% just acknowledge
    for d in range(days):
        for h in range(24):
            t = start + timedelta(days=d, hours=h, minutes=rnd.randint(0, 5))
            out = rnd.randint(5, 25) if rnd.random() < 0.6 else rnd.randint(300, 700)
            rows.append(_row(t, "grok-bot-automation", 30000 + rnd.randint(-2000, 2000), 0.9, out))
    # one-shot cloud runs (not persistent bots)
    for d in range(0, days, 2):
        rows.append(_row(start + timedelta(days=d, hours=12), "grok-4.7-high", 20000, 0.0, 1500, cloud=f"ca-{d}"))
    rows.sort(key=lambda r: r[0], reverse=True)          # the real export is newest first
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(HEADER)
        w.writerows(rows)
    return path


if __name__ == "__main__":
    print(generate(sys.argv[1] if len(sys.argv) > 1 else "grok_usage_events.csv"))
