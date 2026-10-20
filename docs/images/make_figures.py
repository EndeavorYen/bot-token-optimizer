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
    t = {"en": (f"Simulated redesign: 7-day cost ${tb:.0f} → ${ta:.0f} ({res['ratio']:.2f}×)",
                "Removed a relay leader · banned “OK” replies · content in messages, not board round-trips · slim group chat · fresh contexts",
                "Before", "After", "removed"),
         "zh-TW": (f"模擬改組：7 天成本 ${tb:.0f} → ${ta:.0f}（{res['ratio']:.2f} 倍）",
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



def _load_sample(name):
    with open(os.path.join(ROOT, "samples", name), encoding="utf-8") as f:
        return json.load(f)


def _changes():
    """The sample redesign as six separate changes, each turning part of org_before into org_after."""
    after = _load_sample("org_after.json")

    def relay(o):      # remove the relay leader: chief hands work straight to the researcher
        o["bots"] = [b for b in o["bots"] if b["name"] != "lead"]
        o["links"] = [l for l in o["links"] if "lead" not in (l["from"], l["to"])]
        o["links"].insert(0, {"from": "chief", "to": "researcher", "messages_per_day": 6, "avg_tokens": 800})
        for g in o["groups"]:       # the leader's own group posts disappear with it
            keep = [m for m in g["members"] if m != "lead"]
            g["messages_per_day"] = g["messages_per_day"] * len(keep) / len(g["members"])
            g["members"] = keep

    def acks(o):       # no "OK" replies
        o["links"] = [l for l in o["links"] if l["avg_tokens"] > 30]

    def board(o):      # content in the message instead of a board round-trip
        for b in o["bots"]:
            b.pop("reads_board_via_tool", None)
        for l in o["links"]:
            if (l["from"], l["to"]) in (("researcher", "writer"), ("writer", "reviewer")):
                l["avg_tokens"] = 1500

    def group(o):      # one-line group updates
        for g in o["groups"]:
            g["avg_tokens"] = 80

    def reports(o):    # a short verdict to the chief instead of every full report
        for l in o["links"]:
            if (l["from"], l["to"]) == ("reviewer", "chief"):
                l["messages_per_day"], l["avg_tokens"] = 2, 600

    def fresh(o):      # hand over to fresh chats
        ctx = {b["name"]: b["context_tokens"] for b in after["bots"]}
        for b in o["bots"]:
            b["context_tokens"] = ctx.get(b["name"], 15000)

    return {"relay": relay, "acks": acks, "board": board, "group": group, "reports": reports, "fresh": fresh}


def waterfall_steps():
    """Shapley attribution: each change's average saving over every order of applying the six changes.

    A plain step-by-step waterfall depends on the order (removing a bot changes how often the others
    are woken by the group chat, for instance). Averaging over all 720 orders removes that, and the
    contributions still add up exactly to the total saving.
    """
    import copy
    import itertools
    before = _load_sample("org_before.json")
    changes = _changes()
    cache = {}

    def cost(applied):
        key = frozenset(applied)
        if key not in cache:
            o = copy.deepcopy(before)
            for name in changes:                 # fixed internal order; the set is what matters
                if name in key:
                    changes[name](o)
            cache[key] = orgopt.evaluate_org(o, PRICING)["total_usd"]
        return cache[key]

    names = list(changes)
    contrib = dict.fromkeys(names, 0.0)
    perms = list(itertools.permutations(names))
    for perm in perms:
        done = []
        for n in perm:
            prev = cost(done)
            done.append(n)
            contrib[n] += prev - cost(done)
    contrib = {n: v / len(perms) for n, v in contrib.items()}
    total_before, total_after = cost([]), cost(names)
    target = orgopt.evaluate_org(_load_sample("org_after.json"), PRICING)["total_usd"]
    assert abs(total_after - target) < 1e-6, (total_after, target)      # all six changes give org_after
    assert abs(sum(contrib.values()) - (total_before - total_after)) < 1e-6
    ordered = sorted(contrib.items(), key=lambda kv: -kv[1])
    steps, cur = [("start", total_before)], total_before
    for n, v in ordered:
        cur -= v
        steps.append((n, cur))
    return steps


def fig_waterfall(lang, nums):
    steps = waterfall_steps()
    nums["waterfall"] = {k: round(v, 2) for k, v in steps}
    t = {"en": {"title": f"Where the saving comes from: ${steps[0][1]:.0f} → ${steps[-1][1]:.0f} a week",
                "sub": "Simulated sample team (samples/). Each bar is that change's average saving over",
                "sub2": "all 720 orders of applying the six changes (its Shapley value), so the bars add up exactly.",
                "start": "Before", "relay": "Remove the relay leader", "acks": "Stop “OK” replies",
                "board": "Content in the message, not a board", "group": "One-line group updates",
                "reports": "Short verdicts to the chief", "fresh": "Hand over to fresh chats", "end": "After"},
         "zh-TW": {"title": f"省下的錢從哪裡來：每週 ${steps[0][1]:.0f} → ${steps[-1][1]:.0f}",
                   "sub": "模擬範例團隊（samples/）。每根橫條是該項改變在六項改變全部 720 種套用順序下",
                   "sub2": "的平均節省（Shapley 值），所以加總恰好等於總節省。",
                   "start": "改組前", "relay": "拿掉轉話 leader", "acks": "停止「OK」回覆",
                   "board": "內容放訊息，不再讀 board", "group": "群組只貼一行",
                   "reports": "給總管簡短結論", "fresh": "交接給新的 chat", "end": "改組後"}}[lang]
    W, rowh, y0 = 880, 40, 118
    rows = len(steps) + 1
    H = y0 + rows * rowh + 30
    x0, x1 = 330, 790
    S = lambda v: x0 + v / steps[0][1] * (x1 - x0)
    body = header(t["title"], t["sub"]) + [text(32, 90, t["sub2"], 13.5, 400, MUTED)]
    body.append(line(x0, y0 - 8, x0, y0 + rows * rowh - 8, AXIS))
    prev = steps[0][1]
    for i, (key, val) in enumerate(steps):
        y = y0 + i * rowh
        if i == 0:
            body += [text(32, y + 19, t["start"], 13.5, 700), rect(x0, y + 4, S(val) - x0, 22, BAD, 4),
                     text(S(val) + 8, y + 20, f"${val:,.2f}", 13, 700)]
        else:
            drop = prev - val
            body += [text(32, y + 19, t[key], 13.5, 500), line(S(prev), y - rowh + 26, S(prev), y + 4, AXIS, 1, "3 3"),
                     rect(S(val), y + 4, max(S(prev) - S(val), 1.5), 22, GOOD, 4),
                     text(S(prev) + 8, y + 20, f"−${drop:,.2f}", 13, 600, GOOD)]
        prev = val
    y = y0 + len(steps) * rowh
    body += [text(32, y + 19, t["end"], 13.5, 700), line(S(prev), y - rowh + 26, S(prev), y + 4, AXIS, 1, "3 3"),
             rect(x0, y + 4, S(prev) - x0, 22, INK, 4), text(S(prev) + 8, y + 20, f"${prev:,.2f}", 13, 700)]
    return card(W, H, body, t["title"])


MODEL_NAMES = {"grok-4.7": "xAI Grok 4.7", "gpt-6.1-sol": "OpenAI GPT-6.1 Sol", "gpt-6-luna": "OpenAI GPT-6 Luna",
               "claude-opus-5-5": "Claude Opus 5.5", "claude-sonnet-5-5": "Claude Sonnet 5.5",
               "claude-haiku-5-5": "Claude Haiku 5.5", "gemini-4-argon": "Gemini 4 Argon", "gemini-3.8-flash": "Gemini 3.8 Flash"}


def fig_models(lang, nums):
    before, after = _load_sample("org_before.json"), _load_sample("org_after.json")
    rows = []
    for name in orgopt.list_presets("pricing"):
        p = orgopt.load_pricing(name)
        b, a = orgopt.evaluate_org(before, p)["total_usd"], orgopt.evaluate_org(after, p)["total_usd"]
        rows.append((name, b, a))
    rows.sort(key=lambda r: -r[1])
    nums["models"] = {n: {"before": round(b, 2), "after": round(a, 2), "ratio": round(a / b, 3)} for n, b, a in rows}
    lo, hi = min(a / b for _, b, a in rows), max(a / b for _, b, a in rows)
    t = {"en": ("The same habits waste money on every model",
                f"Simulated sample team, one week, before vs. after the redesign · the redesign costs {lo:.0%}–{hi:.0%} of the original",
                "Before", "After"),
         "zh-TW": ("同樣的習慣，在每個模型上都在浪費錢",
                   f"模擬範例團隊一週成本，改組前 vs 改組後 · 改組後只剩原本的 {lo:.0%}～{hi:.0%}",
                   "改組前", "改組後")}[lang]
    W, rowh, y0 = 880, 46, 98
    H = y0 + len(rows) * rowh + 44
    x0, x1 = 230, 720
    mx = max(b for _, b, _ in rows)
    S = lambda v: v / mx * (x1 - x0)
    body = header(t[0], t[1])
    for i, (name, b, a) in enumerate(rows):
        y = y0 + i * rowh
        body += [text(x0 - 14, y + 22, MODEL_NAMES.get(name, name), 13.5, 600, INK, "end"),
                 rect(x0, y + 4, max(S(b), 2), 14, BAD, 3), text(x0 + max(S(b), 2) + 8, y + 16, f"${b:,.2f}", 12, 500),
                 rect(x0, y + 22, max(S(a), 2), 14, GOOD, 3), text(x0 + max(S(a), 2) + 8, y + 34, f"${a:,.2f}", 12, 600, GOOD),
                 text(W - 32, y + 25, f"−{1 - a / b:.0%}", 13.5, 700, GOOD, "end")]
    body += legend([(BAD, t[2], 60), (GOOD, t[3], 0)], x0, H - 18)
    return card(W, H, body, t[0])


FIGURES = {"banner": fig_banner, "context-growth": fig_growth, "relay-leader": fig_leader,
           "board-overhead": fig_board, "before-after": fig_before_after, "span-of-control": fig_span,
           "waterfall": fig_waterfall, "models": fig_models}


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
