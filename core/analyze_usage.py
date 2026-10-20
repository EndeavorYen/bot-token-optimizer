#!/usr/bin/env python3
"""analyze_usage — find where a bot team's tokens go, from an exported usage CSV.

What it does:
  1. Maps columns automatically (time, bot, chat, input / cached / output / reasoning tokens, cost)
  2. Treats each chat as its own context (a bot with several chats has several contexts)
  3. Per context: calls, wake-ups, calls per wake-up, context size C, growth, cache hit rate
  4. Detects hand-overs already made (sudden drops in context) and measures growth between them
  5. Flags: growth toward the long-context tier, low-output wake-ups ("OK"), read-then-work
     wake-ups (board reads), long-context billing, low cache hit rate, cost hotspots
  6. Calibrates the cost model against the actual spend, and writes an org.json skeleton

Standard library only. Use --map field=CSV column when names do not match.
"""
import csv
import json
import math
import statistics
from datetime import datetime, timezone

try:
    from . import orgopt
    from .i18n import t
except ImportError:          # run as a plain script
    import orgopt
    from i18n import t

ALIASES = {
    "time": ["timestamp", "time", "datetime", "date", "created_at", "created", "request_time", "start_time"],
    "bot": ["bot", "bot_name", "botname", "agent", "agent_name", "bot_id", "agent_id", "assistant", "name"],
    "chat": ["chat_id", "chat", "thread_id", "thread", "conversation_id", "session_id", "conversation"],
    "input": ["input_tokens", "prompt_tokens", "input", "total_input_tokens", "tokens_in"],
    "cached": ["cached_input_tokens", "cached_tokens", "cache_read_tokens", "cached_prompt_tokens",
               "cache_read_input_tokens", "cached"],
    "output": ["output_tokens", "completion_tokens", "output", "tokens_out"],
    "reasoning": ["reasoning_tokens", "thinking_tokens", "reasoning"],
    "cost": ["cost_usd", "cost", "price_usd", "amount_usd"],
}
REQUIRED = ["time", "input"]
RESET_DROP = 0.6        # a wake-up whose context is < 60% of the previous one ...
RESET_MIN = 5000        # ... and at least 5K tokens smaller counts as a hand-over


class UsageError(ValueError):
    pass


# ---------------------------------------------------------------- loading
def _norm(s):
    return s.strip().lower().replace(" ", "_").replace("-", "_")


def map_columns(header, overrides=None, lang="en"):
    norm = {_norm(h): h for h in header}
    mapping = {}
    for field, names in ALIASES.items():
        for n in names:
            if n in norm:
                mapping[field] = norm[n]
                break
    for k, v in (overrides or {}).items():
        if v not in header:
            raise UsageError(t("u.err_map", lang, col=v, cols=header))
        mapping[k] = v
    missing = [f for f in REQUIRED if f not in mapping]
    if missing:
        raise UsageError(t("u.err_missing", lang, missing=missing, cols=header))
    return mapping


def parse_time(v):
    v = str(v).strip()
    if not v:
        raise ValueError("empty time")
    try:
        x = float(v)
        if x > 1e12:      # milliseconds
            x /= 1000
        return datetime.fromtimestamp(x, tz=timezone.utc)
    except ValueError:
        pass
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%Y-%m-%d"):
            try:
                d = datetime.strptime(v, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"cannot parse time: {v!r}")
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _num(v):
    v = str(v).strip().replace(",", "")
    if v == "" or v.lower() in ("nan", "null", "none", "-"):
        return 0.0
    return float(v)


def load_rows(path, overrides=None, cached_separate=False, lang="en"):
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(8192)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        mapping = map_columns(reader.fieldnames or [], overrides, lang)
        rows, skipped = [], 0
        for raw in reader:
            try:
                r = {"time": parse_time(raw[mapping["time"]]), "input": _num(raw[mapping["input"]])}
            except (ValueError, KeyError):
                skipped += 1
                continue
            r["bot"] = (raw.get(mapping["bot"]) or "").strip() if "bot" in mapping else ""
            r["chat"] = (raw.get(mapping["chat"]) or "").strip() if "chat" in mapping else ""
            for k in ("cached", "output", "reasoning", "cost"):
                r[k] = _num(raw.get(mapping[k], 0)) if k in mapping else 0.0
            if cached_separate:          # cached tokens reported separately from input
                r["input"] += r["cached"]
            r["cached"] = min(r["cached"], r["input"])
            rows.append(r)
    rows.sort(key=lambda r: r["time"])
    return rows, mapping, skipped


# ---------------------------------------------------------------- analysis
def _fit(xs, ys):
    """Least-squares line: (slope, intercept). Slope is 0 with fewer than 3 points."""
    n = len(xs)
    my = sum(ys) / n
    if n < 3:
        return 0.0, my
    mx = sum(xs) / n
    den = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 0.0
    return slope, my - slope * mx


def sessionize(rows, gap_seconds):
    """Consecutive calls of one context no more than `gap` apart form one wake-up."""
    wakes, cur = [], []
    for r in rows:
        if cur and (r["time"] - cur[-1]["time"]).total_seconds() > gap_seconds:
            wakes.append(cur)
            cur = []
        cur.append(r)
    if cur:
        wakes.append(cur)
    return wakes


def split_resets(days, ctx):
    """Split a wake-up series at hand-overs (sharp context drops). Returns a list of (start, end) index pairs."""
    cuts = [i for i in range(1, len(ctx))
            if ctx[i] < RESET_DROP * ctx[i - 1] and ctx[i - 1] - ctx[i] >= RESET_MIN]
    bounds = [0] + cuts + [len(ctx)]
    return [(bounds[j], bounds[j + 1]) for j in range(len(bounds) - 1)]


def assign_units(rows):
    """Context unit per row: bot, or bot/chat when a bot has several chats, or chat when there is no bot."""
    chats = {}
    for r in rows:
        chats.setdefault(r["bot"], set()).add(r["chat"])
    for r in rows:
        if r["bot"] and len(chats[r["bot"]]) > 1:
            r["unit"] = f"{r['bot']}/{r['chat'] or '-'}"
        else:
            r["unit"] = r["bot"] or r["chat"] or "(all)"


def analyze(rows, pricing, gap_seconds=120, ack_output=30, read_first_output=80, soft_cap=None,
            has_cost=False, lang="en"):
    thr = pricing.get("long_context_threshold") or math.inf
    if not rows:
        raise UsageError(t("u.err_empty", lang))
    assign_units(rows)
    t0, t1 = rows[0]["time"], rows[-1]["time"]
    span_days = max((t1 - t0).total_seconds() / 86400, 1 / 24)
    by_unit = {}
    for r in rows:
        by_unit.setdefault(r["unit"], []).append(r)

    units, total_usd = [], 0.0
    for name, rs in by_unit.items():
        usd = sum(r["cost"] for r in rs) if has_cost else sum(
            orgopt.call_cost_usd(r["input"], r["cached"], r["output"] + r["reasoning"], pricing) for r in rs)
        total_usd += usd
        wakes = sessionize(rs, gap_seconds)
        ctx = [w[0]["input"] for w in wakes]
        days = [(w[0]["time"] - t0).total_seconds() / 86400 for w in wakes]
        segments = []
        for a, b in split_resets(days, ctx):
            slope, icpt = _fit(days[a:b], ctx[a:b])
            segments.append({"start": days[a], "end": days[b - 1] if b - 1 > a else days[a],
                             "n": b - a, "slope": slope, "c0": max(icpt + slope * days[a], 0.0) if b - a >= 3 else ctx[a]})
        fitted = [s for s in segments if s["n"] >= 3 and s["end"] > s["start"]]
        weight = sum(s["end"] - s["start"] for s in fitted)
        growth = sum(s["slope"] * (s["end"] - s["start"]) for s in fitted) / weight if weight else 0.0
        last = ctx[split_resets(days, ctx)[-1][0]:]
        c_now = statistics.median(last[-max(3, len(last) // 5):])
        inp = sum(r["input"] for r in rs)
        cached = sum(r["cached"] for r in rs)
        out = sum(r["output"] for r in rs)
        reasoning = sum(r["reasoning"] for r in rs)
        short = [w for w in wakes if len(w) == 1 and w[0]["output"] <= ack_output]
        read_first = [w for w in wakes if len(w) >= 2 and w[0]["output"] <= read_first_output]
        cost1 = lambda r: orgopt.call_cost_usd(r["input"], r["cached"], r["output"], pricing)
        long_calls = [r for r in rs if r["input"] >= thr]
        flat = dict(pricing, long_context_threshold=None)
        long_extra = sum(cost1(r) - orgopt.call_cost_usd(r["input"], r["cached"], r["output"], flat) for r in long_calls)

        def days_to(target):
            if c_now >= target:
                return 0.0
            return (target - c_now) / growth if growth > 0 else math.inf

        units.append({
            "unit": name, "calls": len(rs), "wakes": len(wakes), "wakes_per_day": len(wakes) / span_days,
            "rounds_per_wake": len(rs) / len(wakes), "C_now": c_now, "C_max": max(ctx),
            "C_growth_per_day": growth, "resets_seen": len(segments) - 1, "segments": segments,
            "days_to_long_context": days_to(thr), "days_to_soft_cap": days_to(soft_cap) if soft_cap else None,
            "cache_hit": cached / inp if inp else 0.0, "input_tokens": inp, "output_tokens": out,
            "reasoning_tokens": reasoning, "output_per_wake": (out + reasoning) / len(wakes), "usd": usd,
            "short_wakes": len(short), "short_wake_usd": sum(cost1(w[0]) for w in short),
            "read_first_wakes": len(read_first), "read_first_usd": sum(cost1(w[0]) for w in read_first),
            "long_context_calls": len(long_calls), "long_context_extra_usd": long_extra,
        })
    for u in units:
        u["share"] = u["usd"] / total_usd if total_usd else 0.0
    units.sort(key=lambda u: -u["usd"])

    findings = []
    n = len(units)
    for u in units:
        if n >= 3 and u["share"] >= max(0.30, 1.5 / n):
            findings.append(("hotspot", u["unit"], t("u.hotspot", lang, p=u["share"]), None, t("u.hotspot.a", lang)))
        if u["short_wakes"] and u["short_wakes"] / u["wakes"] >= 0.15:
            findings.append(("ack", u["unit"], t("u.ack", lang, n=u["short_wakes"], p=u["short_wakes"] / u["wakes"], thr=ack_output),
                             u["short_wake_usd"], t("u.ack.a", lang)))
        if u["read_first_wakes"] and u["read_first_wakes"] / u["wakes"] >= 0.3:
            findings.append(("read_first", u["unit"], t("u.read", lang, n=u["read_first_wakes"], p=u["read_first_wakes"] / u["wakes"]),
                             u["read_first_usd"], t("u.read.a", lang)))
        if u["long_context_calls"]:
            findings.append(("long_context", u["unit"], t("u.long", lang, n=u["long_context_calls"], thr=f"{thr:,.0f}"),
                             u["long_context_extra_usd"], t("u.long.a", lang)))
        elif u["days_to_long_context"] != math.inf and u["days_to_long_context"] <= 14:
            findings.append(("growth", u["unit"], t("u.growth", lang, c=f"{u['C_now']:,.0f}", g=f"{u['C_growth_per_day']:,.0f}",
                                                    d=u["days_to_long_context"], thr=f"{thr:,.0f}"), None, t("u.growth.a", lang)))
        if u["input_tokens"] and u["cache_hit"] < 0.5 and u["calls"] >= 10:
            findings.append(("cache", u["unit"], t("u.cache", lang, p=u["cache_hit"]), None, t("u.cache.a", lang)))
    findings.sort(key=lambda f: -(f[3] or 0))
    return {"calibration": calibrate(units, total_usd, pricing),
            "period_start": t0.isoformat(), "period_end": t1.isoformat(), "span_days": span_days,
            "total_usd": total_usd, "rows": len(rows), "units": units,
            "findings": [dict(zip(("type", "unit", "detail", "usd", "advice"), f)) for f in findings]}


def calibrate(units, actual_usd, pricing):
    """Let the cost model reproduce the period from the measured parameters (segment by segment) and compare."""
    model_usd = 0.0
    for u in units:
        for i, s in enumerate(u["segments"]):
            dur = max(s["end"] - s["start"], 1 / u["wakes_per_day"]) if u["wakes_per_day"] else 0
            if dur <= 0:
                continue
            org = {"horizon_days": dur, "bots": [{
                "name": f"{u['unit']}#{i}", "context_tokens": s["c0"], "own_wakes_per_day": u["wakes_per_day"],
                "work_rounds_per_wake": max(u["rounds_per_wake"] - 1, 0), "cache_hit_ratio": u["cache_hit"],
                "growth_per_day": s["slope"] if s["n"] >= 3 else u["C_growth_per_day"],
                "output_tokens_per_wake": u["output_per_wake"]}]}
            model_usd += orgopt.evaluate_org(org, pricing)["total_usd"]
    err = (model_usd - actual_usd) / actual_usd if actual_usd else math.nan
    return {"model_usd": model_usd, "actual_usd": actual_usd, "error": err}


def org_skeleton(res, horizon_days=7, lang="en"):
    """org.json skeleton from the measured values; links still have to be filled in."""
    bots = [{"name": u["unit"], "context_tokens": round(u["C_now"]), "base_tokens": round(min(u["C_now"], 8000)),
             "own_wakes_per_day": round(u["wakes_per_day"], 2),
             "work_rounds_per_wake": round(max(u["rounds_per_wake"] - 1, 0), 2),
             "output_tokens_per_wake": round(u["output_per_wake"]), "cache_hit_ratio": round(u["cache_hit"], 3),
             "growth_per_day": round(u["C_growth_per_day"])} for u in res["units"]]
    return {"horizon_days": horizon_days, "_note": t("u.skeleton_note", lang), "bots": bots, "links": [], "groups": []}


def report_md(res, lang="en"):
    L = [f"## {t('u.title', lang, start=res['period_start'][:16], end=res['period_end'][:16], days=res['span_days'], rows=res['rows'])}",
         "", t("u.total", lang, usd=res["total_usd"]), "", t("u.unit_note", lang), "",
         t("u.header", lang), "|---|---|---|---|---|---|---|---|---|---|"]
    for u in res["units"]:
        L.append(f"| {u['unit']} | {u['share']:.0%} | {u['calls']} | {u['wakes_per_day']:.1f} | {u['rounds_per_wake']:.1f} | "
                 f"{u['C_now']:,.0f} | {u['C_growth_per_day']:,.0f} | {u['resets_seen']} | {u['cache_hit']:.0%} | ${u['usd']:,.2f} |")
    c = res["calibration"]
    ok = abs(c["error"]) <= 0.15
    space = "" if lang == "zh-TW" else " "
    L += ["", t("u.calib", lang, model=c["model_usd"], actual=c["actual_usd"], err=c["error"]) + space
          + t("u.calib_ok" if ok else "u.calib_bad", lang), "", f"## {t('u.findings', lang)}", ""]
    if not res["findings"]:
        L.append(t("u.none", lang))
    colon = "：" if lang == "zh-TW" else ": "
    end = "。" if lang == "zh-TW" else "."
    gap = "" if lang == "zh-TW" else " "
    for i, f in enumerate(res["findings"], 1):
        s = f"{i}. **[{f['type']}] {f['unit']}**{colon}{f['detail']}{end}{gap}{t('org.advice', lang)}{colon}{f['advice']}{end}"
        if f["usd"]:
            s += gap + t("u.related", lang, usd=f["usd"])
        L.append(s)
    L += ["", t("u.footer", lang)]
    return "\n".join(L)


def notes(mapping, skipped, lang="en"):
    out = [t("u.mapping", lang, m=mapping) + (f"; {t('u.skipped', lang, n=skipped)}" if skipped else "")]
    if "bot" not in mapping:
        out.append(t("u.no_bot", lang) + t("u.use_chat" if "chat" in mapping else "u.all_one", lang))
    if "cached" not in mapping:
        out.append(t("u.no_cache", lang))
    return out


def run(csv_path, pricing, overrides=None, cached_separate=False, gap=120, soft_cap=None, lang="en"):
    rows, mapping, skipped = load_rows(csv_path, overrides, cached_separate, lang)
    res = analyze(rows, pricing, gap, soft_cap=soft_cap, has_cost="cost" in mapping, lang=lang)
    res["column_mapping"], res["skipped_rows"] = mapping, skipped
    return res


if __name__ == "__main__":
    import sys
    try:
        from .cli import main
    except ImportError:
        from cli import main
    sys.exit(main(["analyze"] + sys.argv[1:]))
