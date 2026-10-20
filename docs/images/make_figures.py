#!/usr/bin/env python3
"""Generate the README / guide figures (English + Traditional Chinese) from the cost model.

Every number drawn here comes from core/orgopt.py, so the figures stay consistent
with the scripts. Run from anywhere:  python3 docs/images/make_figures.py
Prints the key numbers as JSON so the docs can quote them.
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "core"))
import orgopt  # noqa: E402

PRICING = orgopt.load_pricing()
LATIN = "Inter, -apple-system, 'Segoe UI', Helvetica, Arial"
CJK = "'Noto Sans CJK TC', 'Noto Sans TC', 'PingFang TC', 'Microsoft JhengHei'"
# Chinese figures list the CJK fonts first so renderers without per-glyph fallback still draw them.
FONTS = {"en": f"{LATIN}, {CJK}, sans-serif", "zh-TW": f"{CJK}, {LATIN}, sans-serif"}
FONT = FONTS["en"]
MONO = "'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace"
INK, MUTED, GRID, AXIS = "#111827", "#6B7280", "#E5E7EB", "#9CA3AF"
BAD, GOOD, WARN, NEUTRAL = "#E4572E", "#2E86AB", "#F2A541", "#A3AAB5"
LANGS = ("en", "zh-TW")


# ------------------------------------------------------------------ svg helpers
def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, s, size=13, weight=400, fill=INK, anchor="start", family=None, extra=""):
    family = family or FONT
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{family}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}" {extra}>{esc(s)}</text>')


def rect(x, y, w, h, fill, rx=0, extra=""):
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0):.1f}" height="{h:.1f}" rx="{rx}" fill="{fill}" {extra}/>'


def line(x1, y1, x2, y2, stroke=GRID, width=1, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" stroke-width="{width}"{d}/>'


def polyline(pts, stroke, width=2.5):
    p = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    return f'<polyline points="{p}" fill="none" stroke="{stroke}" stroke-width="{width}" stroke-linejoin="round" stroke-linecap="round"/>'


def card(w, h, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
            f'aria-label="{esc(title)}">' + rect(0.5, 0.5, w - 1, h - 1, "#FFFFFF", 16, f'stroke="{GRID}"')
            + "".join(body) + "</svg>")


def header(title, subtitle):
    return [text(32, 44, title, 20, 700), text(32, 70, subtitle, 13.5, 400, MUTED)]


def legend(items, x, y, gap=28):
    out, cx = [], x
    for color, label, width in items:
        out.append(rect(cx, y - 10, 14, 14, color, 3))
        out.append(text(cx + 22, y + 2, label, 13, 500))
        cx += 22 + width + gap
    return out


def k_fmt(v):
    return f"{v / 1000:.0f}K"


# ------------------------------------------------------------------ figures
def fig_banner(lang):
    t = {"en": ("Your bots are burning tokens on wake-ups, relays and bloated context.",
                "Find where the tokens go. Fix how your bots talk.",
                "cost ≈ wake-ups × calls per wake-up × context"),
         "zh-TW": ("你的 bot 把 token 浪費在重複喚醒、轉話和越來越大的上下文。",
                   "找出 token 花在哪裡，改掉 bot 之間的溝通方式。",
                   "成本 ≈ 喚醒次數 × 每次喚醒的輪數 × 上下文")}[lang]
    W, H = 880, 250
    body = [rect(0, 0, W, H, "#0F172A", 18)]
    # decorative series on the right: rising (no reset) vs sawtooth (reset)
    x0, x1, yb, yt = 600, 850, 210, 40
    rise = [(x0 + i * (x1 - x0) / 40, yb - i * (yb - yt) / 40) for i in range(41)]
    saw, c = [], 0.0
    for i in range(41):
        if c > 0.42:
            saw.append((x0 + i * (x1 - x0) / 40, yb - c * (yb - yt)))
            c = 0.06
        saw.append((x0 + i * (x1 - x0) / 40, yb - c * (yb - yt)))
        c += 0.05
    body += [line(x0, yb, x1, yb, "#334155"), polyline(rise, BAD, 3), polyline(saw, "#5DADE2", 3)]
    body += [text(40, 74, "bot-token-optimizer", 38, 800, "#F8FAFC", family=MONO)]
    body += [text(40, 118, t[0], 17, 600, "#E2E8F0"), text(40, 146, t[1], 15, 400, "#94A3B8")]
    pill_w = 34 + len(t[2]) * (8.6 if lang == "en" else 12.5)
    body += [rect(40, 172, pill_w, 40, "#1E293B", 20), text(58, 198, t[2], 15, 600, WARN, family=MONO if lang == "en" else None)]
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="bot-token-optimizer">' + "".join(body) + "</svg>"


def growth_series(days=14, c0=20_000, growth=15_000, wakes=10, reset_at=None, c_new=11_000, dt=0.01):
    pts, cost, c, t, resets = [], 0.0, c0, 0.0, 0
    while t <= days + 1e-9:
        pts.append((t, c))
        cost += wakes * dt * orgopt.call_cost_usd(c, 0.8 * c, 0, PRICING)
        c += growth * dt
        if reset_at and c >= reset_at:
            pts.append((t + dt, c))
            cost += orgopt.call_cost_usd(c, 0.8 * c, 3000, PRICING)   # write the hand-over
            c, resets = c_new, resets + 1
        t += dt
    return pts, cost, resets


def fig_growth(lang, nums):
    no_pts, no_cost, _ = growth_series()
    rs_pts, rs_cost, n_res = growth_series(reset_at=90_000)
    nums["growth"] = {"no_reset_usd": round(no_cost, 2), "reset_usd": round(rs_cost, 2),
                      "resets": n_res, "ratio": round(rs_cost / no_cost, 2)}
    t = {"en": ("Context keeps growing until you reset it",
                "Tokens re-read per wake-up · one bot, +15K context/day, 10 wake-ups/day, 80% cache hits",
                "Day", "200K: the whole request is billed at double price",
                f"Never reset — 14-day cost ${no_cost:.2f}", f"Hand over at 90K — ${rs_cost:.2f}"),
         "zh-TW": ("上下文會一直長大，直到你重建它",
                   "每次喚醒要重讀的 token · 單一 bot，上下文每天 +15K，每天喚醒 10 次，快取命中 80%",
                   "天", "200K：整個請求改用兩倍單價",
                   f"從不重建 — 14 天成本 ${no_cost:.2f}", f"到 90K 就交接 — ${rs_cost:.2f}")}[lang]
    W, H = 880, 420
    x0, x1, yt, yb = 86, 840, 100, 330
    X = lambda d: x0 + d / 14 * (x1 - x0)
    Y = lambda c: yb - c / 250_000 * (yb - yt)
    body = header(t[0], t[1])
    for v in range(0, 250_001, 50_000):
        body += [line(x0, Y(v), x1, Y(v)), text(x0 - 10, Y(v) + 4, k_fmt(v) if v else "0", 12, 400, MUTED, "end")]
    for d in range(0, 15, 2):
        body += [text(X(d), yb + 22, d, 12, 400, MUTED, "middle")]
    body += [text((x0 + x1) / 2, yb + 44, t[2], 12.5, 500, MUTED, "middle")]
    body += [line(x0, Y(200_000), x1, Y(200_000), WARN, 2, "6 5"),
             text(x0 + 8, Y(200_000) - 8, t[3], 12.5, 600, "#B7791F")]
    body += [polyline([(X(d), Y(c)) for d, c in no_pts], BAD, 3),
             polyline([(X(d), Y(c)) for d, c in rs_pts], GOOD, 3)]
    body += legend([(BAD, t[4], len(t[4]) * (7.2 if lang == "en" else 11)),
                    (GOOD, t[5], 0)], x0, H - 26)
    return card(W, H, body, t[0])


def fig_leader(lang, nums):
    a = orgopt.scenario_relay(30_000, 60_000, 100_000)
    b = orgopt.scenario_aggregate(6, 30_000, 60_000, 100_000)
    nums["leader"] = {"relay": a, "aggregate": b}
    t = {"en": ("A leader that only relays almost doubles the cost",
                "Total tokens read · worker / leader / chief context = 30K / 60K / 100K",
                "Single task — leader only forwards", "6 parallel subtasks — leader sends one summary up",
                "Flat (direct hand-off)", "With leader"),
         "zh-TW": ("只轉話的 leader 讓成本接近翻倍",
                   "總讀取 token · worker／leader／總管的上下文 = 30K／60K／100K",
                   "單一任務：leader 只轉話", "6 個平行子任務：leader 彙整後交一份摘要",
                   "扁平（直接交接）", "有 leader")}[lang]
    W, H = 880, 330
    bx0, bx1 = 250, 720
    S = lambda v: v / 800_000 * (bx1 - bx0)
    body = header(t[0], t[1])
    rows = [(108, t[2], a), (210, t[3], b)]
    for y, label, r in rows:
        body.append(text(32, y, label, 14.5, 700))
        worse = r["hierarchy"] > r["flat"]
        for i, (lab, v, col) in enumerate([(t[4], r["flat"], NEUTRAL), (t[5], r["hierarchy"], BAD if worse else GOOD)]):
            yy = y + 18 + i * 34
            body += [text(bx0 - 14, yy + 16, lab, 13, 500, MUTED, "end"), rect(bx0, yy, S(v), 24, col, 5)]
            tag = k_fmt(v) + (f"   ×{r['ratio']:.2f}" if i == 1 else "")
            body.append(text(bx0 + S(v) + 10, yy + 17, tag, 13.5, 700 if i == 1 else 500,
                             (BAD if worse else GOOD) if i == 1 else INK))
    return card(W, H, body, t[0])


def fig_board(lang, nums):
    ks = range(0, 5)
    seq = [orgopt.board_overhead(k, "sequential")["token_ratio"] for k in ks]
    par = [orgopt.board_overhead(k, "parallel")["token_ratio"] for k in ks]
    nums["board"] = {"sequential": seq, "parallel": par}
    t = {"en": ("“Write to a board, then ping” costs up to 2× tokens",
                "Token multiplier vs. putting the content in the message · the reader fetches the board with one extra tool call",
                "Rounds of own work per wake-up (k)", "Write, then notify (2 steps)", "Write + notify in one step"),
         "zh-TW": ("「寫 board 再通知」最多多花 2 倍 token",
                   "相對直接把內容放進訊息的 token 倍數 · 接收方多一次工具呼叫去讀 board",
                   "每次喚醒為自己工作做的輪數（k）", "先寫 board 再通知（兩步）", "寫 board 與通知同一步")}[lang]
    W, H = 880, 400
    x0, x1, yt, yb = 86, 840, 110, 310
    Y = lambda v: yb - (v - 1) / 1.1 * (yb - yt)
    body = header(t[0], t[1])
    for v in (1.0, 1.25, 1.5, 1.75, 2.0):
        body += [line(x0, Y(v), x1, Y(v)), text(x0 - 10, Y(v) + 4, f"{v:.2f}×", 12, 400, MUTED, "end")]
    gw = (x1 - x0) / 5
    for i, k in enumerate(ks):
        cx = x0 + gw * (i + 0.5)
        for j, (v, col) in enumerate([(seq[i], BAD), (par[i], WARN)]):
            bx = cx - 44 + j * 48
            body += [rect(bx, Y(v), 40, yb - Y(v), col, 4), text(bx + 20, Y(v) - 7, f"{v + 1e-9:.2f}", 12.5, 600, INK, "middle")]
        body.append(text(cx, yb + 22, f"k = {k}", 12.5, 500, MUTED, "middle"))
    body += [line(x0, yb, x1, yb, AXIS), text((x0 + x1) / 2, yb + 46, t[2], 12.5, 500, MUTED, "middle")]
    body += legend([(BAD, t[3], len(t[3]) * (7.2 if lang == "en" else 12)), (WARN, t[4], 0)], x0, H - 22)
    return card(W, H, body, t[0])


def fig_before_after(lang, nums):
    with open(os.path.join(ROOT, "samples", "org_before.json"), encoding="utf-8") as f:
        before = json.load(f)
    with open(os.path.join(ROOT, "samples", "org_after.json"), encoding="utf-8") as f:
        after = json.load(f)
    res = orgopt.compare_orgs(before, after, PRICING)
    b = {r["bot"]: r["cost_usd"] for r in res["before"]["bots"]}
    a = {r["bot"]: r["cost_usd"] for r in res["after"]["bots"]}
    tb, ta = res["before"]["total_usd"], res["after"]["total_usd"]
    nums["before_after"] = {"before_usd": round(tb, 2), "after_usd": round(ta, 2), "ratio": round(res["ratio"], 2),
                            "per_bot_before": {k: round(v, 2) for k, v in b.items()},
                            "per_bot_after": {k: round(v, 2) for k, v in a.items()}}
    t = {"en": (f"Sample redesign: 7-day cost ${tb:.0f} → ${ta:.0f} ({res['ratio']:.2f}×)",
                "Removed a relay leader · banned “OK” replies · content in messages, not board round-trips · slim group chat · fresh contexts",
                "Before", "After", "removed"),
         "zh-TW": (f"範例改組：7 天成本 ${tb:.0f} → ${ta:.0f}（{res['ratio']:.2f} 倍）",
                   "拿掉轉話 leader · 禁止「OK」回覆 · 內容直接放訊息，不再來回讀 board · 精簡群組 · 重建上下文",
                   "改組前", "改組後", "已移除")}[lang]
    order = sorted(b, key=lambda k: -b[k])
    W, H = 880, 120 + 58 * len(order) + 36
    bx0, bx1 = 170, 740
    S = lambda v: v / max(b.values()) * (bx1 - bx0)
    body = header(t[0], t[1])
    for i, bot in enumerate(order):
        y = 110 + i * 58
        body.append(text(bx0 - 16, y + 26, bot, 14, 600, INK, "end", family=MONO))
        body += [rect(bx0, y + 4, S(b[bot]), 20, BAD, 4), text(bx0 + S(b[bot]) + 8, y + 19, f"${b[bot]:.2f}", 12.5, 500)]
        if bot in a:
            body += [rect(bx0, y + 28, S(a[bot]), 20, GOOD, 4), text(bx0 + S(a[bot]) + 8, y + 43, f"${a[bot]:.2f}", 12.5, 600, GOOD)]
        else:
            body.append(text(bx0 + 4, y + 43, t[4], 12.5, 600, MUTED))
    body += legend([(BAD, t[2], 60), (GOOD, t[3], 0)], bx0, H - 26)
    return card(W, H, body, t[0])


def fig_span(lang, nums):
    c0 = 30_000
    pts_m = [100 * (100 ** (i / 200)) for i in range(201)]          # 100 .. 10,000
    k = lambda m: orgopt.optimal_span_closed(c0, m)
    marks = [(5000, k(5000)), (1000, k(1000)), (200, k(200))]
    nums["span"] = {str(m): round(v, 1) for m, v in marks}
    t = {"en": ("Shorter reports → each manager can handle more bots",
                "Optimal span k* ≈ 1 + √(1 + 2·C0 / m), manager base context C0 = 30K",
                "Report length per message m (tokens, log scale)", "Bots per manager",
                "Grok Bot group limit: 6", ["full report", "summary", "one line + file path"]),
         "zh-TW": ("回報越短，一個主管能帶越多 bot",
                   "最佳控制幅度 k* ≈ 1 + √(1 + 2·C0 / m)，主管基礎上下文 C0 = 30K",
                   "每則回報長度 m（token，對數刻度）", "每個主管帶幾個 bot",
                   "Grok Bot 群組上限：6", ["完整報告", "摘要", "一行狀態 + 檔案路徑"])}[lang]
    W, H = 880, 410
    x0, x1, yt, yb = 86, 830, 100, 320
    X = lambda m: x0 + (math.log10(m) - 2) / 2 * (x1 - x0)
    Y = lambda v: yb - v / 25 * (yb - yt)
    body = header(t[0], t[1])
    for v in (0, 5, 10, 15, 20, 25):
        body += [line(x0, Y(v), x1, Y(v)), text(x0 - 10, Y(v) + 4, v, 12, 400, MUTED, "end")]
    for m, lab in [(100, "100"), (200, "200"), (500, "500"), (1000, "1K"), (2000, "2K"), (5000, "5K"), (10000, "10K")]:
        body.append(text(X(m), yb + 22, lab, 12, 400, MUTED, "middle"))
    body += [text((x0 + x1) / 2, yb + 46, t[2], 12.5, 500, MUTED, "middle"),
             text(24, (yt + yb) / 2, t[3], 12.5, 500, MUTED, "middle",
                  extra=f'transform="rotate(-90 24 {(yt + yb) / 2:.1f})"')]
    body += [line(x0, Y(6), x1, Y(6), WARN, 2, "6 5"), text(x0 + 8, Y(6) - 8, t[4], 12.5, 600, "#B7791F")]
    body.append(polyline([(X(m), Y(k(m))) for m in pts_m], GOOD, 3))
    for i, ((m, v), lab) in enumerate(zip(marks, t[5])):
        below = i == 0                       # the 4.6 point sits under the group-limit line
        body += [f'<circle cx="{X(m):.1f}" cy="{Y(v):.1f}" r="6" fill="#FFFFFF" stroke="{GOOD}" stroke-width="3"/>',
                 text(X(m) - 12 if below else X(m) + 12, Y(v) + 24 if below else Y(v) - 10,
                      f"{lab}: {v:.1f}", 12.5, 600, INK, "end" if below else "start")]
    return card(W, H, body, t[0])


FIGURES = {"banner": fig_banner, "context-growth": fig_growth, "relay-leader": fig_leader,
           "board-overhead": fig_board, "before-after": fig_before_after, "span-of-control": fig_span}


def main():
    global FONT
    nums = {}
    for name, fn in FIGURES.items():
        for lang in LANGS:
            FONT = FONTS[lang]
            svg = fn(lang) if name == "banner" else fn(lang, nums)
            with open(os.path.join(HERE, f"{name}.{lang}.svg"), "w", encoding="utf-8") as f:
                f.write(svg)
    print(json.dumps(nums, ensure_ascii=False, indent=2, default=float))


if __name__ == "__main__":
    main()
