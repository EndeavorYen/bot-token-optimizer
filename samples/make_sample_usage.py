#!/usr/bin/env python3
"""產生合成的 usage CSV，用來測試 analyze_usage.py（固定亂數種子，結果可重現）。

刻意埋入的模式：
  chief      上下文快速成長（會在期間內接近 200K）
  writer     約 40% 喚醒只回一句「收到」
  researcher 每次喚醒第一輪只讀 board、幾乎沒有輸出
  reviewer   快取命中率偏低
"""
import csv
import random
import sys
from datetime import datetime, timedelta, timezone

PROFILES = {
    "chief":      dict(wakes=6, rounds=2, c0=40000, growth=15000, out=400, cache=0.85),
    "writer":     dict(wakes=10, rounds=2, c0=30000, growth=5000, out=600, cache=0.85, ack=0.4),
    "researcher": dict(wakes=8, rounds=3, c0=25000, growth=8000, out=500, cache=0.85, read_first=True),
    "reviewer":   dict(wakes=4, rounds=1, c0=20000, growth=0, out=300, cache=0.30),
}


def generate(path, days=7, seed=7):
    rnd = random.Random(seed)
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rows = []
    for bot, p in PROFILES.items():
        n_wakes = int(p["wakes"] * days)
        for i in range(n_wakes):
            t = start + timedelta(days=days * (i + rnd.random() * 0.5) / n_wakes)
            day = (t - start).total_seconds() / 86400
            c = p["c0"] + p["growth"] * day
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
                             "bot_name": bot, "chat_id": f"{bot}-main",
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
    print(f"寫入 {generate(out)} 筆到 {out}")
