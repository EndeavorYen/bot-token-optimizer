#!/usr/bin/env python3
"""Generate a synthetic usage CSV for testing analyze_usage (fixed seed, reproducible).

Planted patterns:
  chief       context grows fast (approaches 200K within the period)
  writer      ~40% of wake-ups only reply "OK"; the user handed it over to a fresh chat on day 4
  researcher  every wake-up first reads the board, producing almost no output in the first call
  reviewer    low cache hit rate
  support     one bot, two chats: each chat keeps its own context
"""
import csv
import random
import sys
from datetime import datetime, timedelta, timezone

PROFILES = {
    "chief":      dict(wakes=6, rounds=2, c0=40000, growth=15000, out=400, cache=0.85),
    "writer":     dict(wakes=10, rounds=2, c0=30000, growth=5000, out=600, cache=0.85, ack=0.4, reset_day=4, reset_to=12000),
    "researcher": dict(wakes=8, rounds=3, c0=25000, growth=8000, out=500, cache=0.85, read_first=True),
    "reviewer":   dict(wakes=4, rounds=1, c0=20000, growth=0, out=300, cache=0.30),
    "support":    dict(wakes=6, rounds=1, c0=0, growth=0, out=300, cache=0.85,
                       chats={"a": (60000, 6000), "b": (15000, 1000)}),
}


def generate(path, days=7, seed=7):
    rnd = random.Random(seed)
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rows = []
    for bot, p in PROFILES.items():
        for chat, (c0, growth) in (p.get("chats") or {"main": (p["c0"], p["growth"])}).items():
            n_wakes = int(p["wakes"] * days)
            for i in range(n_wakes):
                t = start + timedelta(days=days * (i + rnd.random() * 0.5) / n_wakes)
                day = (t - start).total_seconds() / 86400
                if p.get("reset_day") and day >= p["reset_day"]:
                    c = p["reset_to"] + growth * (day - p["reset_day"])
                else:
                    c = c0 + growth * day
                is_ack = rnd.random() < p.get("ack", 0)
                rounds = 1 if is_ack else p["rounds"]
                for j in range(rounds):
                    inp = int(c + j * 900)
                    cached = int(inp * (p["cache"] if j > 0 else min(p["cache"], 0.5)))
                    if is_ack:
                        out = 8
                    elif p.get("read_first") and j == 0:
                        out = 40
                    else:
                        out = int(p["out"] * (0.8 + 0.4 * rnd.random()))
                    rows.append({"timestamp": (t + timedelta(seconds=20 * j)).isoformat(),
                                 "bot_name": bot, "chat_id": f"{bot}-{chat}" if chat == "main" else chat,
                                 "input_tokens": inp, "cached_input_tokens": cached,
                                 "output_tokens": out, "reasoning_tokens": 0})
    rows.sort(key=lambda r: r["timestamp"])
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return len(rows)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "sample_usage.csv"
    print(f"wrote {generate(out)} rows to {out}")
