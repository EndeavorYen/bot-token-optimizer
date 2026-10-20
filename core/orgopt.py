#!/usr/bin/env python3
"""orgopt — token cost model for teams of persistent bots.

Core assumption: every call to a bot re-reads its whole context C.
    cost ≈ wake-ups × calls per wake-up × C   (+ the output it writes)

Cost unit: "uncached input-token equivalents" (one uncached input token = 1).
A cached token costs cached/input price; an output token costs output/input price.
Multiply by the input price (pricing preset) to get dollars.

Standard library only. The command-line interface lives in cli.py
(`bto ...` or `python3 core/cli.py ...`); `python3 core/orgopt.py ...` still works.
"""
import json
import math
import os

try:
    from .i18n import t, sep
except ImportError:          # run as a plain script
    from i18n import t, sep

HERE = os.path.dirname(os.path.abspath(__file__))
PRICING_DIR = os.path.join(HERE, "pricing")
PLATFORM_DIR = os.path.join(HERE, "platforms")
DEFAULT_PRICING = os.path.join(PRICING_DIR, "grok-4.7.json")
DEFAULT_SPAN_CAP = 8          # generic rule of thumb; platforms with a hard limit override it (Grok Bot: 6)
DEFAULT_HANDOVER = 3000       # hand-over summary length (tokens)
DEFAULT_CACHE_HIT = 0.8
LATENCY_WARN_HOPS = 5


# ---------------------------------------------------------------- presets
def _resolve(name_or_path, folder):
    if name_or_path is None:
        return None
    if os.path.exists(name_or_path):
        return name_or_path
    cand = os.path.join(folder, f"{name_or_path}.json")
    if os.path.exists(cand):
        return cand
    known = sorted(f[:-5] for f in os.listdir(folder) if f.endswith(".json"))
    raise FileNotFoundError(f"{name_or_path!r} not found; built-in presets: {', '.join(known)}")


def list_presets(kind="pricing"):
    folder = PRICING_DIR if kind == "pricing" else PLATFORM_DIR
    return sorted(f[:-5] for f in os.listdir(folder) if f.endswith(".json"))


def load_pricing(name_or_path=None):
    with open(_resolve(name_or_path, PRICING_DIR) or DEFAULT_PRICING, encoding="utf-8") as f:
        return json.load(f)


def load_platform(name_or_path):
    """Load a platform preset by name (e.g. 'grok-bot') or path. Its pricing path is relative to the file."""
    path = _resolve(name_or_path, PLATFORM_DIR)
    with open(path, encoding="utf-8") as f:
        plat = json.load(f)
    if "pricing" in plat:
        plat["pricing_path"] = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(path)), plat["pricing"]))
    return plat


def price_ratios(pricing):
    """(cached-token relative price, output-token relative price, long-context multiplier)."""
    p = pricing["per_million"]
    lp = pricing.get("long_context_per_million") or p
    return p["cached_input"] / p["input"], p["output"] / p["input"], lp["input"] / p["input"]


def long_multipliers(pricing):
    """(input multiplier, output multiplier) once the prompt crosses the long-context threshold."""
    p = pricing["per_million"]
    lp = pricing.get("long_context_per_million") or p
    return lp["input"] / p["input"], lp["output"] / p["output"]


def call_cost_usd(input_tokens, cached_tokens, output_tokens, pricing):
    """Dollar cost of one call. cached_tokens is part of input_tokens."""
    thr = pricing.get("long_context_threshold")
    table = pricing["per_million"]
    if thr and input_tokens >= thr:
        table = pricing.get("long_context_per_million") or table
    cached = min(cached_tokens, input_tokens)
    uncached = input_tokens - cached
    return (uncached * table["input"] + cached * table["cached_input"] + output_tokens * table["output"]) / 1e6


def to_usd(input_eq_tokens, pricing):
    return input_eq_tokens * pricing["per_million"]["input"] / 1e6


# ---------------------------------------------------------------- board overhead
def board_overhead(k, mode="sequential", cache_rate=0.25):
    """Multiplier of "write to a board + short ping + receiver reads it via a tool" vs. sending the content directly.

    k: tool-call rounds of the bot's own work per wake-up (excluding the board read).
    mode: sequential = write, then notify (one more round for the sender);
          parallel   = write and notify in the same round.
    """
    if k < 0:
        raise ValueError("k must be >= 0")
    if mode == "sequential":
        token_ratio = (k + 2) / (k + 1)
    elif mode == "parallel":
        token_ratio = 1 + 1 / (2 * (k + 1))
    else:
        raise ValueError("mode must be 'sequential' or 'parallel'")
    r = cache_rate
    cold = (1 + r * (k + 1)) / (1 + r * k)   # first call of a wake-up misses the cache, the rest hit
    warm = (k + 2) / (k + 1)                 # every call hits the cache
    return {"k": k, "mode": mode, "token_ratio": token_ratio,
            "receiver_cost_ratio_cold_cache": cold, "receiver_cost_ratio_warm_cache": warm,
            "extra_tokens_per_notification": "about one receiver context C"}


# ---------------------------------------------------------------- teaching scenarios
def scenario_relay(c_worker, c_leader, c_chief):
    """Single task, leader only relays. Total input tokens (flat vs. hierarchy)."""
    flat = c_worker + c_chief
    hier = c_worker + 2 * c_leader + c_chief
    return {"flat": flat, "hierarchy": hier, "ratio": hier / flat}


def scenario_aggregate(n, c_worker, c_leader, c_chief):
    """n parallel subtasks, leader collects them and sends one summary up."""
    flat = n * c_worker + n * c_chief
    hier = n * c_worker + (n + 1) * c_leader + c_chief
    return {"flat": flat, "hierarchy": hier, "ratio": hier / flat}


# ---------------------------------------------------------------- span of control
def manager_cost(k, c0, m):
    """Cost for a manager to collect k reports: wake-up i reads C0 + i·m (reports accumulate)."""
    return k * c0 + m * k * (k + 1) / 2


def optimal_span_closed(c0, m):
    """Closed-form approximation k* ≈ 1 + √(1 + 2·C0/m) (ignores assignment wake-ups and integrality)."""
    if m <= 0:
        return math.inf
    return 1 + math.sqrt(1 + 2 * c0 / m)


def _even_sizes(nodes, k):
    groups = math.ceil(nodes / k)
    base, extra = divmod(nodes, groups)
    return [base + 1] * extra + [base] * (groups - extra)


def tree_cost(n, k, c0, m, c0_chief=None, assign_wake=True):
    """Total manager cost for a tree of n workers with at most k reports per manager.

    assign_wake: a non-root manager is also woken once when its parent assigns work (cost C0).
    Returns cost, number of managers and depth (layers = rounds of waiting).
    """
    if n < 2 or k < 2:
        raise ValueError("n and k must both be >= 2")
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
    """Cheapest k in 2..min(n, k_max) (numerical, authoritative)."""
    upper = max(2, min(n, k_max or n))
    table = [tree_cost(n, k, c0, m, c0_chief, assign_wake) for k in range(2, upper + 1)]
    best = min(table, key=lambda r: (r["cost"], r["depth"]))
    return {"best": best, "table": table, "closed_form_k": optimal_span_closed(c0, m), "k_cap": k_max}


# ---------------------------------------------------------------- reset threshold
def reset_decision(c, c_base, handover=DEFAULT_HANDOVER, wakes_per_day=10, horizon_days=7,
                   rounds_per_wake=1, cache_hit=DEFAULT_CACHE_HIT, pricing=None, soft_cap=None, lang="en"):
    """Should the bot write a hand-over and continue in a fresh chat / new bot?

    Old bot per wake-up: rounds × p × C, with p = hit × cached price + misses.
    One-off reset cost: the old bot reads C once to write the hand-over (p×C), the hand-over output
    (H × output price), and the new bot's first uncached read of C_new = C_base + H.
    Savings assume C stops growing (conservative).
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
        reasons.append(t("r.long_now", lang, thr=f"{thr:,}"))
    if soft_cap and c >= soft_cap:
        reasons.append(t("r.soft_now", lang, cap=f"{soft_cap:,.0f}"))
    if expected > breakeven and c >= 3 * c_new:
        reasons.append(t("r.breakeven", lang, n=expected, b=breakeven))
    return {"c": c, "c_new": c_new, "rebuild_cost_eq": rebuild, "saving_per_wake_eq": saving_per_wake,
            "breakeven_wakes": breakeven, "expected_wakes": expected, "net_saving_eq": net,
            "net_saving_usd": to_usd(net, pricing), "recommend": bool(reasons), "reasons": reasons}


# ---------------------------------------------------------------- organization
def longest_chain(links, max_nodes=30):
    """Longest simple hand-off path (in hops) through the links graph; None if the graph is too big."""
    adj = {}
    for ln in links:
        adj.setdefault(ln["from"], set()).add(ln["to"])
    nodes = set(adj) | {d for v in adj.values() for d in v}
    if not adj or len(nodes) > max_nodes:
        return None
    best = []

    def dfs(node, path):
        nonlocal best
        if len(path) > len(best):
            best = list(path)
        for nxt in sorted(adj.get(node, ())):
            if nxt not in path:
                path.append(nxt)
                dfs(nxt, path)
                path.pop()
            elif nxt == path[0] and len(path) + 1 > len(best):   # a loop back to the start counts once
                best = path + [nxt]

    for start in sorted(adj):
        dfs(start, [start])
    return {"hops": len(best) - 1, "path": best}


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
        "out": float(b.get("output_tokens_per_wake", 300)) + float(b.get("reasoning_tokens_per_wake", 0)),
        "cache_hit": float(b.get("cache_hit_ratio", DEFAULT_CACHE_HIT)),
        "growth_override": b.get("growth_per_day"),
    }


def evaluate_org(org, pricing=None, span_cap=None, lang="en"):
    """Evaluate an organization description: per-bot cost (input + output) and diagnosis."""
    pricing = pricing or load_pricing()
    r_cache, r_out, _ = price_ratios(pricing)
    long_in, long_out = long_multipliers(pricing)
    thr = pricing.get("long_context_threshold") or math.inf
    H = float(org.get("horizon_days", 7))
    span_cap = int(org.get("span_cap") or span_cap or DEFAULT_SPAN_CAP)
    handover = float(org.get("handover_tokens", DEFAULT_HANDOVER))
    soft_cap = org.get("quality_soft_cap")
    handover_at = org.get("handover_at")
    bots = {b["name"]: _bot_defaults(b) for b in org["bots"]}
    links = org.get("links", []) or []
    groups = org.get("groups", []) or []

    for b in bots.values():
        b.update(msg_wakes=0.0, growth=0.0, in_src=set(), out_dst=set(), in_tokens=[], out_tokens=[])
    for ln in links:
        a, b = ln["from"], ln["to"]
        for x in (a, b):
            if x not in bots:
                raise ValueError(t("v.unknown_bot", lang, where="links", name=x))
        n, tok = float(ln["messages_per_day"]), float(ln["avg_tokens"])
        bots[b]["msg_wakes"] += n
        bots[b]["growth"] += n * tok
        bots[b]["in_src"].add(a)
        bots[b]["in_tokens"].append(tok)
        bots[a]["out_dst"].add(b)
        bots[a]["out_tokens"].append(tok)
    for g in groups:
        mem = g["members"]
        n, tok = float(g["messages_per_day"]), float(g["avg_tokens"])
        wake_all = g.get("wake", "addressed") == "all"
        for x in mem:
            if x not in bots:
                raise ValueError(t("v.unknown_bot", lang, where="groups", name=x))
            bots[x]["growth"] += n * tok
            bots[x]["msg_wakes"] += n if wake_all else n / len(mem)

    total = total_out = 0.0
    for b in bots.values():
        b["wakes"] = b["own"] + b["msg_wakes"]
        extra_board = b["msg_wakes"] if b["reads_board"] else 0.0
        b["rounds_per_day"] = b["wakes"] * (1 + b["k"]) + extra_board
        b["growth"] += b["wakes"] * b["out"] + extra_board * b["board_read"]
        if b["growth_override"] is not None:
            b["growth"] = float(b["growth_override"])
        b["C_end"] = b["C"] + b["growth"] * H
        p = b["cache_hit"] * r_cache + (1 - b["cache_hit"])
        cin = cout = 0.0
        n_steps = max(1, int(H * 24))
        dt = H / n_steps
        b["resets"] = 0
        if handover_at:
            # policy: hand over whenever the context passes handover_at; each hand-over reads the old
            # context once, writes the note and starts the new chat with one uncached read
            ct0 = b["C"]
            for i in range(n_steps):
                ct = ct0 + b["growth"] * 0.5 * dt
                long = ct >= thr
                cin += b["rounds_per_day"] * dt * ct * p * (long_in if long else 1.0)
                cout += b["wakes"] * dt * b["out"] * r_out * (long_out if long else 1.0)
                ct0 += b["growth"] * dt
                if ct0 >= handover_at:
                    c_new = b["base"] + handover
                    cin += p * ct0 + c_new
                    cout += handover * r_out
                    ct0 = c_new
                    b["resets"] += 1
            b["C_end"] = ct0
        else:
          for i in range(n_steps):
            ct = b["C"] + b["growth"] * (i + 0.5) * dt
            long = ct >= thr
            cin += b["rounds_per_day"] * dt * ct * p * (long_in if long else 1.0)
            cout += b["wakes"] * dt * b["out"] * r_out * (long_out if long else 1.0)
        b["input_eq"], b["output_eq"], b["cost_eq"] = cin, cout, cin + cout
        b["per_round_eq"] = cin / (b["rounds_per_day"] * H) if b["rounds_per_day"] else 0.0
        b["per_wake_eq"] = b["per_round_eq"] * (1 + b["k"]) + (cout / (b["wakes"] * H) if b["wakes"] else 0.0)
        total += cin + cout
        total_out += cout

    findings = []

    def add(kind, who, detail, advice, saving=None):
        f = {"type": kind, "bot": who, "detail": detail, "advice": advice}
        if saving:
            f["saving_eq"] = saving
        findings.append(f)

    for b in bots.values():
        share = b["cost_eq"] / total if total else 0
        b["share"] = share
        if len(bots) >= 3 and share >= max(0.30, 1.5 / len(bots)):
            add("hotspot", b["name"], t("f.hotspot", lang, share=share), t("a.hotspot", lang))
        in_msgs = sum(float(ln["messages_per_day"]) for ln in links if ln["to"] == b["name"])
        out_msgs = sum(float(ln["messages_per_day"]) for ln in links if ln["from"] == b["name"])
        if (len(b["in_src"]) == 1 and len(b["out_dst"]) == 1 and b["k"] <= 1
                and b["own"] == 0 and in_msgs and out_msgs >= 0.7 * in_msgs
                and max(b["out_tokens"]) >= 0.6 * max(b["in_tokens"])):
            add("relay", b["name"], t("f.relay", lang, up=next(iter(b["in_src"])), down=next(iter(b["out_dst"]))),
                t("a.relay", lang), b["cost_eq"])
        if len(b["in_src"]) > span_cap:
            add("span", b["name"], t("f.span", lang, n=len(b["in_src"]), cap=span_cap), t("a.span", lang))
        if b["reads_board"] and b["msg_wakes"] > 0:
            add("board_tool_read", b["name"], t("f.board", lang), t("a.board", lang), b["msg_wakes"] * H * b["per_round_eq"])
        # economics use today's C (old and new bot then grow at the same rate, so the gap stays)
        rd = reset_decision(b["C"], b["base"], handover, b["wakes"], H, 1 + b["k"], b["cache_hit"], pricing, soft_cap, lang)
        reasons = list(rd["reasons"])
        if b["C"] < thr <= b["C_end"]:
            reasons.append(t("r.long_soon", lang, thr=f"{thr:,.0f}"))
        if soft_cap and b["C"] < soft_cap <= b["C_end"]:
            reasons.append(t("r.soft_soon", lang, cap=f"{soft_cap:,.0f}"))
        target = min(0.75 * thr, soft_cap or math.inf)
        interval = (target - rd["c_new"]) / b["growth"] if b["growth"] > 0 and target > rd["c_new"] else math.inf
        b["reset"] = dict(rd, reasons=reasons, reset_interval_days=interval)
        if reasons:
            parts = [t("f.reset", lang, c=f"{b['C']:,.0f}", c_end=f"{b['C_end']:,.0f}", c_new=f"{rd['c_new']:,.0f}")]
            parts += reasons
            if interval != math.inf:
                parts.append(t("f.reset.cycle", lang, d=interval))
            add("reset", b["name"], sep(lang).join(parts), t("a.reset", lang), max(rd["net_saving_eq"], 0))
        elif b["C_end"] >= 0.75 * thr:
            add("long_context", b["name"], t("f.long", lang, c_end=f"{b['C_end']:,.0f}", thr=f"{thr:,.0f}"), t("a.long", lang))

    for ln in links:
        if float(ln["avg_tokens"]) <= 30:
            r = bots[ln["to"]]
            add("ack", ln["to"], t("f.ack", lang, src=ln["from"], dst=ln["to"], n=float(ln["messages_per_day"]),
                                   tok=float(ln["avg_tokens"])),
                t("a.ack", lang), float(ln["messages_per_day"]) * H * r["per_wake_eq"])
    for g in groups:
        if float(g["avg_tokens"]) > 500:
            add("group_heavy", g["name"], t("f.group", lang, tok=float(g["avg_tokens"]), n=len(g["members"])), t("a.group", lang))
    chain = longest_chain(links)
    if chain and chain["hops"] >= int(org.get("latency_warn_hops", LATENCY_WARN_HOPS)):
        add("latency", chain["path"][0], t("f.latency", lang, hops=chain["hops"], chain=" → ".join(chain["path"])),
            t("a.latency", lang))

    order = {"relay": 0, "ack": 1, "board_tool_read": 2, "reset": 3, "hotspot": 4, "span": 5,
             "group_heavy": 6, "latency": 7, "long_context": 8}
    findings.sort(key=lambda f: (0 if f.get("saving_eq") else 1, -f.get("saving_eq", 0), order.get(f["type"], 9)))
    rows = [{"bot": b["name"], "C_now": b["C"], "C_end": b["C_end"], "wakes_per_day": b["wakes"],
             "rounds_per_day": b["rounds_per_day"], "input_eq": b["input_eq"], "output_eq": b["output_eq"],
             "cost_eq": b["cost_eq"], "cost_usd": to_usd(b["cost_eq"], pricing), "share": b["share"],
             "reset_interval_days": b["reset"]["reset_interval_days"], "resets": b["resets"]}
            for b in sorted(bots.values(), key=lambda x: -x["cost_eq"])]
    return {"horizon_days": H, "total_eq": total, "total_usd": to_usd(total, pricing),
            "output_usd": to_usd(total_out, pricing), "longest_chain": chain, "bots": rows, "findings": findings}


def compare_orgs(a, b, pricing=None, span_cap=None, lang="en"):
    ra, rb = evaluate_org(a, pricing, span_cap, lang), evaluate_org(b, pricing, span_cap, lang)
    return {"before": ra, "after": rb, "delta_eq": rb["total_eq"] - ra["total_eq"],
            "ratio": rb["total_eq"] / ra["total_eq"] if ra["total_eq"] else math.nan}


# ---------------------------------------------------------------- reports
def _fmt(x):
    return f"{x:,.0f}"


def org_report_md(res, lang="en"):
    L = [f"## {t('org.title', lang, days=res['horizon_days'])}", "",
         t("org.total", lang, eq=_fmt(res["total_eq"]), usd=res["total_usd"], out_usd=res["output_usd"]), "",
         t("org.header", lang), "|---|---|---|---|---|---|---|"]
    for r in res["bots"]:
        L.append(f"| {r['bot']} | {_fmt(r['C_now'])} | {_fmt(r['C_end'])} | {r['wakes_per_day']:.1f} | "
                 f"{r['rounds_per_day']:.1f} | {r['share']:.0%} | ${r['cost_usd']:,.2f} |")
    if res.get("longest_chain"):
        ch = res["longest_chain"]
        L += ["", t("org.chain", lang, hops=ch["hops"], chain=" → ".join(ch["path"]))]
    L += ["", f"## {t('org.findings', lang)}", "", t("org.overlap", lang), ""]
    if not res["findings"]:
        L.append(t("org.none", lang))
    colon = "：" if lang == "zh-TW" else ": "
    end = "。" if lang == "zh-TW" else "."
    gap = "" if lang == "zh-TW" else " "
    for i, f in enumerate(res["findings"], 1):
        s = f"{i}. **[{f['type']}] {f['bot']}**{colon}{f['detail']}{end}{gap}{t('org.advice', lang)}{colon}{f['advice']}{end}"
        if f.get("saving_eq"):
            s += t("org.saving", lang, eq=_fmt(f["saving_eq"]))
        L.append(s)
    return "\n".join(L)


def compare_report_md(res, lang="en"):
    head = t("org.compare", lang, a=_fmt(res["before"]["total_eq"]), b=_fmt(res["after"]["total_eq"]),
             ratio=res["ratio"], delta=_fmt(res["delta_eq"]))
    return f"{head}\n\n### {t('org.after', lang)}\n\n{org_report_md(res['after'], lang)}"


if __name__ == "__main__":
    import sys
    try:
        from .cli import main
    except ImportError:
        from cli import main
    sys.exit(main())
