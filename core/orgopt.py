#!/usr/bin/env python3
"""orgopt — 多 bot 組織的 token 成本模型（已驗證的計算腳本）。

核心假設：bot 每被呼叫一輪，就要重讀一次完整上下文 C。
  成本 ≈ 喚醒次數 × 每次喚醒的輪數 × C

成本單位：「未快取 input token 等值」（1 個未快取 input token = 1）。
  快取 token = cached_input 價 / input 價；output token = output 價 / input 價。
  需要美元時，乘上 input 單價（pricing/*.json）。

只用 Python 標準函式庫，可在任何有 python3 的環境執行。
子命令：board / scenario / span / reset / org / compare / cost
平台差異（價格、群組人數上限）放在 adapters/<平台>/platform.json，用 --platform 指定。
"""
import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_PRICING = os.path.join(ROOT, "pricing", "grok-4.7.json")
DEFAULT_SPAN_CAP = 8          # 通用預設上限；平台有硬限制時由 platform.json 覆寫（Grok Bot 為 6）
DEFAULT_HANDOVER = 3000       # 交接摘要長度（token）
DEFAULT_CACHE_HIT = 0.8       # 預設快取命中比例


# ---------------------------------------------------------------- 價格
def load_pricing(path=None):
    path = path or DEFAULT_PRICING
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_platform(path):
    """讀取平台設定。pricing 路徑以 platform.json 所在資料夾為基準。"""
    with open(path, encoding="utf-8") as f:
        plat = json.load(f)
    if "pricing" in plat:
        plat["pricing_path"] = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(path)), plat["pricing"]))
    return plat


def price_ratios(pricing):
    """回傳 (快取 token 相對價, output token 相對價, 長上下文倍數)。"""
    p = pricing["per_million"]
    lp = pricing.get("long_context_per_million", p)
    return p["cached_input"] / p["input"], p["output"] / p["input"], lp["input"] / p["input"]


def call_cost_usd(input_tokens, cached_tokens, output_tokens, pricing):
    """單次呼叫的美元成本。cached_tokens 視為 input_tokens 的一部分。"""
    thr = pricing.get("long_context_threshold")
    table = pricing["per_million"]
    if thr and input_tokens >= thr:
        table = pricing.get("long_context_per_million", table)
    cached = min(cached_tokens, input_tokens)
    uncached = input_tokens - cached
    return (uncached * table["input"] + cached * table["cached_input"]
            + output_tokens * table["output"]) / 1e6


def to_usd(input_eq_tokens, pricing):
    return input_eq_tokens * pricing["per_million"]["input"] / 1e6


# ---------------------------------------------------------------- board 額外成本
def board_overhead(k, mode="sequential", cache_rate=0.25):
    """「寫 board + 短訊息 + 對方用 tool 讀」相對「直接講」的倍數。

    k：每次喚醒為了自己的工作做幾輪工具呼叫（不含讀 board 那一輪）。
    mode：sequential = 發送方先寫 board 再通知（多一輪）；
          parallel   = 寫 board 與通知在同一輪平行發出。
    回傳 token 倍數（發送方+接收方），以及接收方的成本倍數（冷/熱快取）。
    """
    if k < 0:
        raise ValueError("k 不可為負")
    if mode == "sequential":
        token_ratio = (k + 2) / (k + 1)
    elif mode == "parallel":
        token_ratio = 1 + 1 / (2 * (k + 1))
    else:
        raise ValueError("mode 必須是 sequential 或 parallel")
    r = cache_rate
    cold = (1 + r * (k + 1)) / (1 + r * k)   # 喚醒第一輪快取失效，其餘命中
    warm = (k + 2) / (k + 1)                 # 每輪都命中快取
    return {"k": k, "mode": mode, "token_ratio": token_ratio,
            "receiver_cost_ratio_cold_cache": cold,
            "receiver_cost_ratio_warm_cache": warm,
            "extra_tokens_per_notification": "約 1 個接收方的 C"}


# ---------------------------------------------------------------- 教學情境
def scenario_relay(c_worker, c_leader, c_chief):
    """單一任務：leader 只轉話。回傳 (扁平, 階層) 的總 input。"""
    flat = c_worker + c_chief
    hier = c_worker + 2 * c_leader + c_chief
    return {"flat": flat, "hierarchy": hier, "ratio": hier / flat}


def scenario_aggregate(n, c_worker, c_leader, c_chief):
    """n 個平行子任務：leader 收齊後交一份摘要給總管。"""
    flat = n * c_worker + n * c_chief
    hier = n * c_worker + (n + 1) * c_leader + c_chief
    return {"flat": flat, "hierarchy": hier, "ratio": hier / flat}


# ---------------------------------------------------------------- 控制幅度
def manager_cost(k, c0, m):
    """主管收齊 k 則回報的成本：第 i 次喚醒讀 C0 + i·m（回報會累積在上下文）。"""
    return k * c0 + m * k * (k + 1) / 2


def optimal_span_closed(c0, m):
    """封閉解近似：k* ≈ 1 + √(1 + 2·C0/m)。忽略派工喚醒與整數限制。"""
    if m <= 0:
        return math.inf
    return 1 + math.sqrt(1 + 2 * c0 / m)


def _even_sizes(nodes, k):
    groups = math.ceil(nodes / k)
    base, extra = divmod(nodes, groups)
    return [base + 1] * extra + [base] * (groups - extra)


def tree_cost(n, k, c0, m, c0_chief=None, assign_wake=True):
    """n 個 worker、每個主管最多帶 k 人的樹，把結果往上彙整的主管總成本。

    assign_wake：非根主管被上層派工時也會喚醒一次（成本 C0）。
    c0_chief：根（總管）的基礎上下文，未給則同 c0。
    回傳 dict：cost、managers、depth（層數 = 延遲輪數）。
    """
    if n < 2 or k < 2:
        raise ValueError("n 與 k 都至少要 2")
    levels, nodes = [], n
    while True:
        sizes = _even_sizes(nodes, k)
        levels.append(sizes)
        if len(sizes) == 1:
            break
        nodes = len(sizes)
    cost, managers = 0.0, 0
    for li, sizes in enumerate(levels):
        is_root = li == len(levels) - 1
        for s in sizes:
            c = c0_chief if (is_root and c0_chief is not None) else c0
            cost += manager_cost(s, c, m)
            if assign_wake and not is_root:
                cost += c
            managers += 1
    return {"k": k, "cost": cost, "managers": managers, "depth": len(levels)}


def optimal_span(n, c0, m, c0_chief=None, k_max=DEFAULT_SPAN_CAP, assign_wake=True):
    """在 2..min(n, k_max) 之間找總成本最低的 k（數值解，權威答案）。"""
    upper = max(2, min(n, k_max))
    table = [tree_cost(n, k, c0, m, c0_chief, assign_wake) for k in range(2, upper + 1)]
    best = min(table, key=lambda r: (r["cost"], r["depth"]))
    return {"best": best, "table": table,
            "closed_form_k": optimal_span_closed(c0, m),
            "k_cap": k_max}


# ---------------------------------------------------------------- 重建門檻
def reset_decision(c, c_base, handover=DEFAULT_HANDOVER, wakes_per_day=10,
                   horizon_days=7, rounds_per_wake=1, cache_hit=DEFAULT_CACHE_HIT,
                   pricing=None, soft_cap=None):
    """是否該讓 bot 交接後開新 chat / 新 bot。

    舊 bot 每次喚醒成本：rounds × p × C，p = 快取命中×快取相對價 + 未命中。
    重建一次性成本：舊 bot 讀一次 C 寫交接（p×C）+ 交接摘要輸出（H×output 相對價）
                   + 新 bot 第一次未快取讀 C_new。
    C_new = C_base + H。省下的量以「C 不再成長」計，屬保守估計。
    """
    pricing = pricing or load_pricing()
    r_cache, r_out, _ = price_ratios(pricing)
    p = cache_hit * r_cache + (1 - cache_hit)
    c_new = c_base + handover
    rebuild = p * c + handover * r_out + c_new
    saving_per_wake = rounds_per_wake * p * (c - c_new)
    breakeven = rebuild / saving_per_wake if saving_per_wake > 0 else math.inf
    expected = wakes_per_day * horizon_days
    net = expected * saving_per_wake - rebuild
    thr = pricing.get("long_context_threshold")
    reasons = []
    if thr and c >= thr:
        reasons.append(f"上下文已達 {thr:,} 長上下文計價門檻，單價翻倍")
    if soft_cap and c >= soft_cap:
        reasons.append(f"上下文超過品質軟上限 {soft_cap:,}")
    if expected > breakeven and c >= 3 * c_new:
        reasons.append(f"預計喚醒 {expected:g} 次，超過回本次數 {breakeven:.1f} 次")
    return {"c": c, "c_new": c_new, "rebuild_cost_eq": rebuild,
            "saving_per_wake_eq": saving_per_wake, "breakeven_wakes": breakeven,
            "expected_wakes": expected, "net_saving_eq": net,
            "net_saving_usd": to_usd(net, pricing),
            "recommend": bool(reasons), "reasons": reasons}


# ---------------------------------------------------------------- 組織評估
def _bot_defaults(b):
    c = b["context_tokens"]
    reads = bool(b.get("reads_board_via_tool", False))
    return {
        "name": b["name"], "C": float(c),
        "base": float(b.get("base_tokens", min(c, 8000))),
        "own": float(b.get("own_wakes_per_day", 0)),
        "k": float(b.get("work_rounds_per_wake", 1)),
        "reads_board": reads,
        "board_read": float(b.get("board_read_tokens", 2000 if reads else 0)),
        "out": float(b.get("output_tokens_per_wake", 300)),
        "cache_hit": float(b.get("cache_hit_ratio", DEFAULT_CACHE_HIT)),
        # 實測的上下文成長（token/天）；給了就取代模型推算的成長
        "growth_override": b.get("growth_per_day"),
    }


def evaluate_org(org, pricing=None, span_cap=None):
    """評估一份組織描述（org.json），回傳每個 bot 的成本與診斷。"""
    pricing = pricing or load_pricing()
    r_cache, _, long_mult = price_ratios(pricing)
    thr = pricing.get("long_context_threshold") or math.inf
    H = float(org.get("horizon_days", 7))
    span_cap = int(org.get("span_cap") or span_cap or DEFAULT_SPAN_CAP)
    handover = float(org.get("handover_tokens", DEFAULT_HANDOVER))
    soft_cap = org.get("quality_soft_cap")
    bots = {b["name"]: _bot_defaults(b) for b in org["bots"]}
    links = org.get("links", [])
    groups = org.get("groups", [])

    for name in bots:
        bots[name].update(msg_wakes=0.0, growth=0.0, in_src=set(), out_dst=set(),
                          in_tokens=[], out_tokens=[])
    for ln in links:
        a, b = ln["from"], ln["to"]
        for x in (a, b):
            if x not in bots:
                raise ValueError(f"links 參照了不存在的 bot：{x}")
        n, t = float(ln["messages_per_day"]), float(ln["avg_tokens"])
        bots[b]["msg_wakes"] += n
        bots[b]["growth"] += n * t
        bots[b]["in_src"].add(a)
        bots[b]["in_tokens"].append(t)
        bots[a]["out_dst"].add(b)
        bots[a]["out_tokens"].append(t)
    for g in groups:
        mem = g["members"]
        n, t = float(g["messages_per_day"]), float(g["avg_tokens"])
        wake_all = g.get("wake", "addressed") == "all"
        for x in mem:
            if x not in bots:
                raise ValueError(f"groups 參照了不存在的 bot：{x}")
            bots[x]["growth"] += n * t
            bots[x]["msg_wakes"] += n if wake_all else n / len(mem)

    steps_per_day = 24
    total = 0.0
    for b in bots.values():
        b["wakes"] = b["own"] + b["msg_wakes"]
        extra_board = b["msg_wakes"] if b["reads_board"] else 0.0
        b["rounds_per_day"] = b["wakes"] * (1 + b["k"]) + extra_board
        b["growth"] += b["wakes"] * b["out"] + extra_board * b["board_read"]
        if b["growth_override"] is not None:
            b["growth"] = float(b["growth_override"])
        b["C_end"] = b["C"] + b["growth"] * H
        p = b["cache_hit"] * r_cache + (1 - b["cache_hit"])
        cost = 0.0
        n_steps = max(1, int(H * steps_per_day))
        dt = H / n_steps
        for i in range(n_steps):
            ct = b["C"] + b["growth"] * (i + 0.5) * dt
            mult = long_mult if ct >= thr else 1.0
            cost += b["rounds_per_day"] * dt * ct * p * mult
        b["cost_eq"] = cost
        b["per_round_eq"] = cost / (b["rounds_per_day"] * H) if b["rounds_per_day"] else 0.0
        b["per_wake_eq"] = b["per_round_eq"] * (1 + b["k"])
        total += cost

    findings = []
    for b in bots.values():
        share = b["cost_eq"] / total if total else 0
        b["share"] = share
        if len(bots) >= 3 and share >= max(0.30, 1.5 / len(bots)):
            findings.append({"type": "hotspot", "bot": b["name"],
                             "detail": f"占總成本 {share:.0%}，是最貴的節點",
                             "advice": "減少它的喚醒次數（只在里程碑或例外時通知）、讓下屬交摘要、優先重建它的上下文"})
        in_msgs = sum(float(l["messages_per_day"]) for l in links if l["to"] == b["name"])
        out_msgs = sum(float(l["messages_per_day"]) for l in links if l["from"] == b["name"])
        if (len(b["in_src"]) == 1 and len(b["out_dst"]) == 1 and b["k"] <= 1
                and b["own"] == 0 and in_msgs and out_msgs >= 0.7 * in_msgs
                and max(b["out_tokens"]) >= 0.6 * max(b["in_tokens"])):
            findings.append({"type": "relay", "bot": b["name"],
                             "detail": f"只有一個上游（{next(iter(b['in_src']))}）和一個下游（{next(iter(b['out_dst']))}），且往下傳的內容與收到的差不多長",
                             "advice": "疑似純傳話：讓上下游直接交接，或改成壓縮摘要後再往下傳",
                             "saving_eq": b["cost_eq"]})
        if len(b["in_src"]) > span_cap:
            findings.append({"type": "span", "bot": b["name"],
                             "detail": f"直接接收 {len(b['in_src'])} 個來源的訊息，超過控制幅度 {span_cap}",
                             "advice": "加一層會彙整的 leader，或讓來源先合併回報"})
        if b["reads_board"] and b["msg_wakes"] > 0:
            saving = b["msg_wakes"] * H * b["per_round_eq"]
            findings.append({"type": "board_tool_read", "bot": b["name"],
                             "detail": "被通知後用 tool 讀 board，每次通知多一輪完整上下文",
                             "advice": "小內容直接放在訊息裡；大內容在通知中註明要讀哪一段",
                             "saving_eq": saving})
        # 經濟面用「現在」的 C 判斷（新舊 bot 之後以相同速度成長，差距固定）
        rd = reset_decision(b["C"], b["base"], handover, b["wakes"], H,
                            1 + b["k"], b["cache_hit"], pricing, soft_cap)
        reasons = list(rd["reasons"])
        if b["C"] < thr <= b["C_end"]:
            reasons.append(f"依目前成長速度，期間內會越過 {thr:,.0f} 計價門檻")
        if soft_cap and b["C"] < soft_cap <= b["C_end"]:
            reasons.append(f"期間內會超過品質軟上限 {soft_cap:,.0f}")
        target = min(0.75 * thr, soft_cap or math.inf)
        interval = ((target - rd["c_new"]) / b["growth"]
                    if b["growth"] > 0 and target > rd["c_new"] else math.inf)
        b["reset"] = dict(rd, reasons=reasons, reset_interval_days=interval)
        if reasons:
            cyc = f"；建議約每 {interval:.1f} 天重建一次" if interval != math.inf else ""
            findings.append({"type": "reset", "bot": b["name"],
                             "detail": f"目前上下文 {b['C']:,.0f}（期末約 {b['C_end']:,.0f}），重建後約 {rd['c_new']:,.0f}；"
                                       + "；".join(reasons) + cyc,
                             "advice": "請它寫交接摘要，開新 chat 或新 bot 接手",
                             "saving_eq": max(rd["net_saving_eq"], 0)})
        elif b["C_end"] >= 0.75 * thr:
            findings.append({"type": "long_context", "bot": b["name"],
                             "detail": f"期末上下文約 {b['C_end']:,.0f}，接近 {thr:,.0f} 計價門檻",
                             "advice": "在越過門檻前交接重建"})

    for ln in links:
        if float(ln["avg_tokens"]) <= 30:
            r = bots[ln["to"]]
            saving = float(ln["messages_per_day"]) * H * r["per_wake_eq"]
            findings.append({"type": "ack", "bot": ln["to"],
                             "detail": f"{ln['from']} → {ln['to']} 每天 {ln['messages_per_day']} 則、平均 {ln['avg_tokens']} token，疑似「收到/OK」確認訊息",
                             "advice": "在 bot 指令中禁止純確認訊息；合併通知",
                             "saving_eq": saving})
    for g in groups:
        if float(g["avg_tokens"]) > 500:
            findings.append({"type": "group_heavy", "bot": g["name"],
                             "detail": f"群組平均每則 {g['avg_tokens']} token，每則都進入 {len(g['members'])} 個成員的上下文",
                             "advice": "長內容改走一對一或共享檔案，群組只留一行狀態"})

    order = {"relay": 0, "ack": 1, "board_tool_read": 2, "reset": 3, "hotspot": 4,
             "span": 5, "group_heavy": 6, "long_context": 7}
    # 有估算省量的排前面（由大到小），其餘依類型排序
    findings.sort(key=lambda f: (0 if f.get("saving_eq") else 1, -f.get("saving_eq", 0),
                                 order.get(f["type"], 9)))
    rows = []
    for b in sorted(bots.values(), key=lambda x: -x["cost_eq"]):
        rows.append({"bot": b["name"], "C_now": b["C"], "C_end": b["C_end"],
                     "wakes_per_day": b["wakes"], "rounds_per_day": b["rounds_per_day"],
                     "cost_eq": b["cost_eq"], "cost_usd": to_usd(b["cost_eq"], pricing),
                     "share": b["share"]})
    return {"horizon_days": H, "total_eq": total, "total_usd": to_usd(total, pricing),
            "bots": rows, "findings": findings}


# ---------------------------------------------------------------- 輸出格式
def _fmt(x):
    return f"{x:,.0f}"


def org_report_md(res):
    L = [f"## 組織成本評估（{res['horizon_days']:g} 天）", "",
         f"總成本：約 {_fmt(res['total_eq'])} token 等值（約 ${res['total_usd']:,.2f}）", "",
         "| Bot | 目前 C | 期末 C | 喚醒/天 | 呼叫輪數/天 | 成本占比 | 等值美元 |",
         "|---|---|---|---|---|---|---|"]
    for r in res["bots"]:
        L.append(f"| {r['bot']} | {_fmt(r['C_now'])} | {_fmt(r['C_end'])} | {r['wakes_per_day']:.1f} | "
                 f"{r['rounds_per_day']:.1f} | {r['share']:.0%} | ${r['cost_usd']:,.2f} |")
    L += ["", "## 診斷與建議", "",
          "（各項預估省量會互相重疊，不能直接相加；改組方案請用 compare 驗證總量。）", ""]
    if not res["findings"]:
        L.append("沒有發現明顯的反模式。")
    for i, f in enumerate(res["findings"], 1):
        s = f"{i}. **[{f['type']}] {f['bot']}**：{f['detail']}。建議：{f['advice']}。"
        if f.get("saving_eq"):
            s += f" 預估可省約 {_fmt(f['saving_eq'])} token 等值。"
        L.append(s)
    return "\n".join(L)


def compare_orgs(a, b, pricing=None, span_cap=None):
    ra, rb = evaluate_org(a, pricing, span_cap), evaluate_org(b, pricing, span_cap)
    return {"before": ra, "after": rb, "delta_eq": rb["total_eq"] - ra["total_eq"],
            "ratio": rb["total_eq"] / ra["total_eq"] if ra["total_eq"] else math.nan}


# ---------------------------------------------------------------- CLI
def _out(obj, as_json):
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
    else:
        print(obj)


def main(argv=None):
    ap = argparse.ArgumentParser(description="多 bot 組織 token 成本模型")
    ap.add_argument("--pricing", help="價格設定檔路徑（預設 pricing/grok-4.7.json）")
    ap.add_argument("--platform", help="平台設定，例如 adapters/grok-bot/platform.json")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("board", help="board+短訊息相對直接講的倍數")
    s.add_argument("--k", type=int, default=1)
    s.add_argument("--mode", choices=["sequential", "parallel"], default="sequential")

    s = sub.add_parser("scenario", help="教學情境：relay 或 aggregate")
    s.add_argument("kind", choices=["relay", "aggregate"])
    s.add_argument("--n", type=int, default=6)
    s.add_argument("--c-worker", type=float, default=30000)
    s.add_argument("--c-leader", type=float, default=60000)
    s.add_argument("--c-chief", type=float, default=100000)

    s = sub.add_parser("span", help="最佳控制幅度")
    s.add_argument("--n", type=int, required=True, help="worker 數量")
    s.add_argument("--c0", type=float, required=True, help="主管基礎上下文")
    s.add_argument("--m", type=float, required=True, help="每則回報長度")
    s.add_argument("--c0-chief", type=float)
    s.add_argument("--k-max", type=int, help="控制幅度上限（預設取平台設定，否則 8）")
    s.add_argument("--no-assign-wake", action="store_true")

    s = sub.add_parser("reset", help="是否該交接重建")
    s.add_argument("--c", type=float, required=True)
    s.add_argument("--c-base", type=float, default=8000)
    s.add_argument("--handover", type=float, default=DEFAULT_HANDOVER)
    s.add_argument("--wakes-per-day", type=float, default=10)
    s.add_argument("--horizon-days", type=float, default=7)
    s.add_argument("--rounds-per-wake", type=float, default=1)
    s.add_argument("--cache-hit", type=float, default=DEFAULT_CACHE_HIT)
    s.add_argument("--soft-cap", type=float)

    s = sub.add_parser("org", help="評估 org.json")
    s.add_argument("file")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("compare", help="比較兩份 org.json（改組前後）")
    s.add_argument("before")
    s.add_argument("after")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("cost", help="單次呼叫的美元成本")
    s.add_argument("--input", type=float, required=True)
    s.add_argument("--cached", type=float, default=0)
    s.add_argument("--output", type=float, default=0)

    a = ap.parse_args(argv)
    plat = load_platform(a.platform) if a.platform else {}
    pricing = load_pricing(a.pricing or plat.get("pricing_path"))
    cap = plat.get("span_cap", DEFAULT_SPAN_CAP)
    if a.cmd == "board":
        r_cache, _, _ = price_ratios(pricing)
        _out(board_overhead(a.k, a.mode, r_cache), True)
    elif a.cmd == "scenario":
        if a.kind == "relay":
            _out(scenario_relay(a.c_worker, a.c_leader, a.c_chief), True)
        else:
            _out(scenario_aggregate(a.n, a.c_worker, a.c_leader, a.c_chief), True)
    elif a.cmd == "span":
        _out(optimal_span(a.n, a.c0, a.m, a.c0_chief, a.k_max or cap, not a.no_assign_wake), True)
    elif a.cmd == "reset":
        _out(reset_decision(a.c, a.c_base, a.handover, a.wakes_per_day, a.horizon_days,
                            a.rounds_per_wake, a.cache_hit, pricing, a.soft_cap), True)
    elif a.cmd == "org":
        with open(a.file, encoding="utf-8") as f:
            res = evaluate_org(json.load(f), pricing, cap)
        _out(res, True) if a.json else print(org_report_md(res))
    elif a.cmd == "compare":
        with open(a.before, encoding="utf-8") as f1, open(a.after, encoding="utf-8") as f2:
            res = compare_orgs(json.load(f1), json.load(f2), pricing, cap)
        if a.json:
            _out(res, True)
        else:
            print(f"改組前：{_fmt(res['before']['total_eq'])}　改組後：{_fmt(res['after']['total_eq'])}"
                  f"　變化：{res['ratio']:.2f} 倍（{_fmt(res['delta_eq'])}）\n")
            print("### 改組後\n")
            print(org_report_md(res["after"]))
    elif a.cmd == "cost":
        print(f"{call_cost_usd(a.input, a.cached, a.output, pricing):.6f}")


if __name__ == "__main__":
    sys.exit(main())
