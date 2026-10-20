#!/usr/bin/env python3
"""Generate a synthetic Grok Bot usage-events export (fixed seed, reproducible).

Same columns as the real export: Date, Cloud Agent ID, Automation ID, Kind, Model, Max Mode,
Input (w/ Cache Write), Input (w/o Cache Write), Cache Read, Output Tokens, Total Tokens, Cost.

Planted situation (the common real-world one):
  - Automation ID is empty everywhere, so bots can only be grouped by model
  - grok-bot-default carries most of the spend:
      monitor   a scheduled bot woken every 30 minutes; ~75% of its checks find nothing to do, and its
                long chat is auto-summarized between ~60K and ~165K tokens
      digest    a scheduled bot at 09:00 and 18:00 (UTC+8) doing real work
      three interactive chats (you and bot-to-bot pings), busier in the second half of the week
  - grok-bot-automation: heartbeat, an hourly check that mostly only acknowledges
  - a few cloud-agent rows (excluded by the analyzer); Cost is "Included" (plan-billed)

The matching schedules file is samples/grok_schedules.txt (times in UTC+8).
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
    # monitor: every 30 minutes, one long chat, mostly nothing to do
    mon = 90000.0
    t = start
    while t < start + timedelta(days=days):
        mon += 650 + rnd.uniform(-300, 300)
        if mon > 165000:
            mon = 60000 + rnd.uniform(-2000, 2000)
        w = t + timedelta(seconds=rnd.randint(5, 90))
        if rnd.random() < 0.75:
            rows.append(_row(w, "grok-bot-default", int(mon), 0.2, rnd.randint(8, 40)))
        else:
            for k in range(rnd.choice([2, 3])):
                rows.append(_row(w + timedelta(seconds=30 * k), "grok-bot-default", int(mon + 1500 * k),
                                 0.2 if k == 0 else 0.85, rnd.randint(300, 700)))
        t += timedelta(minutes=30)
    # digest: 09:00 and 18:00 in UTC+8 (01:00 and 10:00 UTC), three rounds of real work
    dig = 40000.0
    for d in range(days):
        for hh in (1, 10):
            dig += 3000
            w = start + timedelta(days=d, hours=hh, seconds=rnd.randint(5, 90))
            for k in range(3):
                rows.append(_row(w + timedelta(seconds=40 * k), "grok-bot-default", int(dig + 2000 * k),
                                 0.2 if k == 0 else 0.85, rnd.randint(600, 1200)))
    # heartbeat (automation): 24 short checks a day, 60% just acknowledge
    for d in range(days):
        for h in range(24):
            t = start + timedelta(days=d, hours=h, seconds=rnd.randint(5, 240))
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
