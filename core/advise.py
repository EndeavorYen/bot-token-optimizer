#!/usr/bin/env python3
"""advise — turn a usage export into what a cost consultant would hand over.

    trend   spend per day, same-window comparison with the day before, what changed
    gaps    what the data cannot tell us, and how to get it
    plan    ranked actions with $/week, evidence tier, confidence and effort
    memo    a one-page memo: bottom line, where the money goes, recommendations,
            decisions and data needed, KPIs with a check date, appendix

Every number comes from analyze_usage / orgopt; this module only ranks and words them.
Weekly rates, savings and quota math use active time: the calendar span minus
quota-exhaustion gaps. A silence longer than `--max-gap` (default 12 h) counts
only when it breaks the export's own daily or weekly activity clock.
Evidence tiers:
    measured  dollars already spent on the pattern in the export (range: remove half to all of it)
    modeled   cost-model projection (confidence drops when calibration is off by more than 15%)
    judgment  sensitivity range (e.g. 10-30% fewer wake-ups), not a forecast
Standard library only.
"""
import bisect
import math
from datetime import datetime, timedelta, timezone

try:
    from . import analyze_usage, orgopt, schedules
    from .i18n import t
except ImportError:          # run as a plain script
    import analyze_usage
    import orgopt
    import schedules
    from i18n import t

CONF_W = {"high": 1.0, "medium": 0.6, "low": 0.3}
EFFORT_W = {"low": 1.0, "medium": 2.0, "high": 3.0}
SHORT_SPAN_DAYS = 2.0
MIN_SHARE = 0.01            # an action must be worth at least 1% of weekly spend
HOTSPOT_SHARE = 0.4
SPLIT_SHARE = 0.3           # a mixed bucket or unscheduled traffic this large is worth breaking down next
FRESH_BASE = 8000           # a fresh chat's starting context (instructions + memory), tokens
CALIB_OK = 0.15
ACK_OUTPUT = 30             # same threshold as analyze_usage's acknowledgement wake-ups
IDLE_OUTPUT = 80            # a single-call wake-up writing at most this much found nothing to do
MATCH_EARLY = 60            # a scheduled wake-up may start this many seconds before the fire time ...
MATCH_WINDOW = 300          # ... or up to this many seconds after it
BOARD_MIN_READS = 10       # read-then-work wake-ups needed before judging a board's size
BOARD_BIG = 4000            # a fetch this large (median) means a whole board, not a section, is read
BOARD_SECTION = 2000        # what reading only the needed section usually brings in
BOARD_CAP = 3000            # a maintained board stays under this
BOARD_GROWING = 1.3         # fetches 30% larger in the second half: the board is growing
FREQ_IDLE = 0.4             # propose a lower frequency when at least 40% of scheduled checks are idle


def parse_tz(s):
    """'+08:00', '-5', 'UTC+8', 'UTC' -> tzinfo."""
    if not s:
        return timezone.utc
    v = s.strip().upper()
    if v in ("UTC", "Z", "GMT"):
        return timezone.utc
    for p in ("UTC", "GMT"):
        if v.startswith(p):
            v = v[len(p):]
    sign = -1 if v.startswith("-") else 1
    h, _, m = v.lstrip("+-").partition(":")
    try:
        return timezone(sign * timedelta(hours=int(h), minutes=int(m or 0)))
    except ValueError:
        raise ValueError(f"cannot parse time zone {s!r}; use e.g. +08:00")


def _row_usd(r, pricing, has_cost):
    if has_cost:
        return r["cost"]
    return orgopt.call_cost_usd(r["input"], r["cached"], r["output"] + r.get("reasoning", 0.0), pricing)


def _rate_days(res):
    """Denominator for per-day and per-week figures: calendar span minus quota gaps."""
    days = res.get("active_days", res["span_days"])
    return days if days > 0 else res["span_days"]


def _active_midpoint(t0, t1, gaps):
    """Instant at which half the active time (span with zero-row gaps removed) has elapsed."""
    intervals, cursor = [], t0
    for g in gaps:
        if g["start"] > cursor:
            intervals.append((cursor, g["start"]))
        if g["end"] > cursor:
            cursor = g["end"]
    if t1 > cursor:
        intervals.append((cursor, t1))
    total = sum((b - a).total_seconds() for a, b in intervals)
    half, acc = total / 2, 0.0
    for a, b in intervals:
        dur = (b - a).total_seconds()
        if acc + dur >= half:
            return a + timedelta(seconds=half - acc)
        acc += dur
    return t1


# ---------------------------------------------------------------- trend
def trend(rows, pricing, has_cost=False, tz=timezone.utc, max_gap_hours=None):
    """Rows must already have gone through analyze_usage.analyze (unit and wake_start set)."""
    t0, t1 = rows[0]["time"], rows[-1]["time"]
    first, last = t0.astimezone(tz), t1.astimezone(tz)
    days = {}
    for r in rows:
        lt = r["time"].astimezone(tz)
        d = lt.date().isoformat()
        e = days.setdefault(d, {"date": d, "usd": 0.0, "calls": 0, "wakes": 0, "units": {}})
        c = _row_usd(r, pricing, has_cost)
        e["usd"] += c
        e["calls"] += 1
        e["wakes"] += bool(r.get("wake_start"))
        e["units"][r.get("unit", "")] = e["units"].get(r.get("unit", ""), 0.0) + c
    cursor = first.date()
    while cursor <= last.date():
        key = cursor.isoformat()
        if key not in days:
            days[key] = {"date": key, "usd": 0.0, "calls": 0, "wakes": 0, "units": {}}
        cursor += timedelta(days=1)
    table = [days[k] for k in sorted(days)]
    edges = (first.date().isoformat(), last.date().isoformat())
    for e in table:
        e["nodata"] = e["calls"] == 0
        e["partial"] = e["date"] in edges and not e["nodata"]
        if e["nodata"]:
            e["top_unit"], e["top_share"] = "—", 0.0
        else:
            top = max(e["units"].items(), key=lambda kv: kv[1])
            e["top_unit"], e["top_share"] = top[0], (top[1] / e["usd"] if e["usd"] else 0.0)
        del e["units"]

    same = None
    yday = last.date() - timedelta(days=1)
    if first.date() <= yday:
        cutoff = last.time()
        y_usd = sum(_row_usd(r, pricing, has_cost) for r in rows
                    if r["time"].astimezone(tz).date() == yday and r["time"].astimezone(tz).time() <= cutoff)
        t_usd = days[last.date().isoformat()]["usd"]
        same = {"day": last.date().isoformat(), "until": last.strftime("%H:%M"), "today_usd": t_usd,
                "yesterday_usd": y_usd, "change": (t_usd - y_usd) / y_usd if y_usd else math.nan,
                "yesterday_partial": first.date() == yday}

    raw_gaps = analyze_usage.detect_usage_gaps(
        rows, analyze_usage.DEFAULT_MAX_GAP_HOURS if max_gap_hours is None else max_gap_hours)
    span_days, active_days = analyze_usage.span_and_active_days(t0, t1, raw_gaps)
    total = sum(e["usd"] for e in table)
    changes = []
    if span_days >= SHORT_SPAN_DAYS:
        # Split on active time so a quota hole in one half is not reported as a drop.
        mid = _active_midpoint(t0, t1, raw_gaps) if raw_gaps else t0 + (t1 - t0) / 2
        half = active_days / 2
        acc = {}
        for r in rows:
            k = 0 if r["time"] < mid else 1
            a = acc.setdefault(r.get("unit", ""), [[0.0, 0, 0.0], [0.0, 0, 0.0]])
            a[k][0] += _row_usd(r, pricing, has_cost)
            if r.get("wake_start"):
                a[k][1] += 1
                a[k][2] += r["input"]
        for unit, ((u0, w0, c0), (u1, w1, c1)) in acc.items():
            changes.append({"unit": unit, "usd_day_before": u0 / half, "usd_day_after": u1 / half,
                            "wakes_day_before": w0 / half, "wakes_day_after": w1 / half,
                            "context_before": c0 / w0 if w0 else 0.0, "context_after": c1 / w1 if w1 else 0.0})
        changes.sort(key=lambda c: -abs(c["usd_day_after"] - c["usd_day_before"]))
    usage_gaps = [{"start": g["start"].isoformat(), "end": g["end"].isoformat(), "days": g["days"],
                   "start_local": g["start"].astimezone(tz).strftime("%Y-%m-%d %H:%M"),
                   "end_local": g["end"].astimezone(tz).strftime("%Y-%m-%d %H:%M")} for g in raw_gaps]
    return {"tz": _tz_name(tz), "days": table, "same_window": same, "changes": changes[:5],
            "span_days": span_days, "active_days": active_days, "usage_gaps": usage_gaps,
            "usd_per_day": total / active_days, "usd_per_week": total / active_days * 7}


def _tz_name(tz):
    off = tz.utcoffset(None)
    if not off:
        return "UTC"
    mins = int(off.total_seconds() // 60)
    sign = "+" if mins >= 0 else "-"
    return f"UTC{sign}{abs(mins) // 60:02d}:{abs(mins) % 60:02d}"


def trend_md(tr, lang="en"):
    L = [f"## {t('tr.title', lang, tz=tr['tz'])}", "", t("tr.header", lang), "|---|---|---|---|---|---|"]
    for e in tr["days"]:
        if e.get("nodata"):
            note = t("tr.nodata", lang)
        elif e["partial"]:
            note = t("tr.partial", lang)
        else:
            note = ""
        L.append(f"| {e['date']} | ${e['usd']:,.2f} | {e['calls']:,} | {e['wakes']:,} | "
                 f"{e['top_unit']} ({e['top_share']:.0%}) | {note} |")
    L += ["", t("tr.rate", lang, wk=tr["usd_per_week"], day=tr["usd_per_day"])]
    if tr.get("usage_gaps"):
        L.append(t("tr.rate_active", lang, active=tr["active_days"], span=tr["span_days"],
                   gap=sum(g["days"] for g in tr["usage_gaps"])))
        for g in tr["usage_gaps"]:
            L.append(t("tr.gap", lang, start=g["start_local"], end=g["end_local"], days=g["days"]))
    s = tr["same_window"]
    if s and not math.isnan(s["change"]):
        L += ["", t("tr.same", lang, day=s["day"], until=s["until"], a=s["today_usd"], b=s["yesterday_usd"], chg=s["change"])
              + (t("tr.same_partial", lang) if s["yesterday_partial"] else "")]
    if tr["changes"]:
        title = t("tr.changes_active", lang) if tr.get("usage_gaps") else t("tr.changes", lang)
        L += ["", f"### {title}", "", t("tr.ch_header", lang), "|---|---|---|---|"]
        for c in tr["changes"]:
            L.append(f"| {c['unit']} | ${c['usd_day_before']:,.2f} → ${c['usd_day_after']:,.2f} | "
                     f"{c['wakes_day_before']:.1f} → {c['wakes_day_after']:.1f} | "
                     f"{c['context_before']:,.0f} → {c['context_after']:,.0f} |")
    return "\n".join(L)


# ---------------------------------------------------------------- schedule attribution
def _wakes(rows, gap):
    by = {}
    for r in rows:
        by.setdefault(r["unit"], []).append(r)
    out = []
    for unit, rs in by.items():
        for w in analyze_usage.sessionize(rs, gap):
            out.append({"unit": unit, "start": w[0]["time"], "rows": w})
    out.sort(key=lambda w: w["start"])
    return out


def wake_stats(wakes, pricing, has_cost, rate_days):
    """Spend and shape of a set of wake-ups (one bot's, or what is left of a bucket)."""
    rows = [r for w in wakes for r in w["rows"]]
    n = len(wakes)
    if not n:
        return None
    usd = sum(_row_usd(r, pricing, has_cost) for r in rows)
    ctx = [w["rows"][0]["input"] for w in wakes]
    inp = sum(r["input"] for r in rows)
    idle = [w for w in wakes if len(w["rows"]) == 1 and w["rows"][0]["output"] <= IDLE_OUTPUT]
    short = [w["rows"][0] for w in wakes if len(w["rows"]) == 1 and w["rows"][0]["output"] <= ACK_OUTPUT]
    rf = [w["rows"] for w in wakes if len(w["rows"]) >= 2 and w["rows"][0]["output"] <= IDLE_OUTPUT]
    t0 = wakes[0]["start"]
    series_growth, _ = analyze_usage.growth_per_day([(w["start"] - t0).total_seconds() / 86400 for w in wakes], ctx)
    return {**analyze_usage.fetch_stats(rf), "read_first_wakes": len(rf),
            "growth_per_wake": sum(analyze_usage.wake_growth(w["rows"]) for w in wakes) / n,
            "series_growth_per_day": max(series_growth, 0.0),
            "short_wakes": len(short), "short_usd": sum(orgopt.call_cost_usd(r["input"], r["cached"], r["output"], pricing) for r in short),"wakes": n, "usd": usd, "usd_week": usd * 7 / rate_days, "per_wake": usd / n,
            "wakes_per_day": n / rate_days, "rounds_per_wake": len(rows) / n,
            "C_p50": analyze_usage.percentile(ctx, 0.5), "C_p90": analyze_usage.percentile(ctx, 0.9),
            "cache_hit": sum(r["cached"] for r in rows) / inp if inp else 0.0,
            "idle_wakes": len(idle), "idle_share": len(idle) / n,
            "idle_usd": sum(_row_usd(w["rows"][0], pricing, has_cost) for w in idle),
            "long_premium_usd": sum(analyze_usage.cost_parts(r, pricing)[3] for r in rows)}


def attribute(rows, res, specs, pricing, has_cost=False, tz=timezone.utc, gap=120,
              early=MATCH_EARLY, window=MATCH_WINDOW):
    """Give wake-ups to the bots whose schedule fired just before them (one wake-up per fire, nearest
    first). What is left in a bucket is unscheduled: the user's messages and bot-to-bot pings."""
    wakes = _wakes(rows, gap)
    starts = [w["start"] for w in wakes]
    t0, t1 = rows[0]["time"], rows[-1]["time"]
    rate_days = _rate_days(res)
    fires, pairs = {}, []
    for sp in specs:
        fires[sp["name"]] = schedules.fire_times(sp, t0 - timedelta(seconds=window), t1, tz)
        for f in fires[sp["name"]]:
            lo = bisect.bisect_left(starts, f - timedelta(seconds=early))
            hi = bisect.bisect_right(starts, f + timedelta(seconds=window))
            for i in range(lo, hi):
                if sp["in"] and wakes[i]["unit"] != sp["in"]:
                    continue
                pairs.append((abs((starts[i] - f).total_seconds()), sp["name"], f, i))
    pairs.sort(key=lambda p: (p[0], p[1]))
    taken_w, taken_f, got = set(), set(), {sp["name"]: [] for sp in specs}
    for _, name, f, i in pairs:
        if i in taken_w or (name, f) in taken_f:
            continue
        taken_w.add(i)
        taken_f.add((name, f))
        got[name].append(i)
    span_s = max((t1 - t0).total_seconds(), 1.0)
    bots = []
    for sp in specs:
        eligible = [i for i, w in enumerate(wakes) if not sp["in"] or w["unit"] == sp["in"]]
        free = sum(1 for i in eligible if i not in taken_w)
        chance = free / span_s * (early + window) * len(fires[sp["name"]])
        st = wake_stats([wakes[i] for i in got[sp["name"]]], pricing, has_cost, rate_days)
        n = len(got[sp["name"]])
        bots.append({"name": sp["name"], "schedule": sp["text"], "spec": sp,
                     "interval_min": schedules.interval_minutes(sp), "fires": len(fires[sp["name"]]),
                     "matched": n, "match_rate": n / len(fires[sp["name"]]) if fires[sp["name"]] else 0.0,
                     "chance_matches": chance, "reliable": n >= 5 and n >= 3 * chance,
                     "units": sorted({wakes[i]["unit"] for i in got[sp["name"]]}), "stats": st})
    bots.sort(key=lambda b: -(b["stats"]["usd"] if b["stats"] else 0))
    touched = {u for b in bots for u in b["units"]}
    remainder = []
    for unit in sorted(touched):
        rest = [w for i, w in enumerate(wakes) if w["unit"] == unit and i not in taken_w]
        st = wake_stats(rest, pricing, has_cost, rate_days)
        if st:
            remainder.append({"unit": unit, "stats": st})
    total = res["total_usd"] or 1.0
    for b in bots:
        b["share"] = b["stats"]["usd"] / total if b["stats"] else 0.0
    for r in remainder:
        r["share"] = r["stats"]["usd"] / total
    return {"bots": bots, "remainder": remainder, "touched_units": sorted(touched),
            "window_s": window, "early_s": early,
            "matched_share": sum(b["share"] for b in bots)}


# ---------------------------------------------------------------- data gaps
def gaps(res, mapping, info, lang="en"):
    """What the export cannot tell us. Each: code, severity (high/medium/low), detail, fix."""
    out = []

    def add(code, sev, **kw):
        out.append({"code": code, "severity": sev, "label": t(f"g.{code}.label", lang),
                    "detail": t(f"g.{code}", lang, **kw), "fix": t(f"g.{code}.fix", lang)})

    loaded = info.get("rows_loaded") or res["rows"]
    if "bot" not in mapping:
        add("no_bot", "high")
    elif info.get("bot_empty_rows") and info["bot_empty_rows"] / loaded >= 0.2:
        buckets = sorted({u["unit"] for u in res["units"] if u.get("from_model")}) or ["-"]
        add("bot_empty", "high" if info["bot_empty_rows"] / loaded >= 0.5 else "medium",
            p=info["bot_empty_rows"] / loaded, col=mapping["bot"], units=", ".join(buckets[:4]))
    if "chat" not in mapping or info.get("chat_column_empty"):
        add("no_chat", "medium")
    if "cached" not in mapping:
        add("no_cache", "high")
    if res["span_days"] < SHORT_SPAN_DAYS:
        add("short", "medium", d=res["span_days"])
    err = res["calibration"]["error"]
    if not math.isnan(err) and abs(err) > CALIB_OK:
        add("calib", "medium", err=err)
    if info.get("cost_text_rows"):
        add("plan_billed", "low")
    return out


# ---------------------------------------------------------------- plan
def _top_units(units, key, n=2):
    picked = sorted((u for u in units if u[key] > 0), key=lambda u: -u[key])[:n]
    return ", ".join(u["unit"] for u in picked) or "-"


HANDOVER_THRESHOLDS = (40000, 60000, 80000, 100000, 120000, 150000)
MAX_HANDOVERS_WEEK = 7      # per context: at most about one hand-over a day unless the client says otherwise


def quota_math(week_usd, days_now, target_days=7, save_low=None, save_high=None):
    """The quota lasts `days_now` days at `week_usd`; what budget makes it last `target_days`, and how long
    it lasts after saving `save_low`..`save_high` a week. All in the same units as week_usd."""
    if days_now <= 0 or target_days <= 0:
        raise ValueError("days must be positive")
    quota = week_usd * days_now / 7
    out = {"days_now": days_now, "target_days": target_days, "quota_usd": quota,
           "budget_usd_week": quota * 7 / target_days, "cut_usd_week": week_usd - quota * 7 / target_days,
           "cut_share": 1 - days_now / target_days}
    for key, save in (("low", save_low), ("high", save_high)):
        if save is not None:
            left = week_usd - save
            out[f"days_after_{key}"] = quota / (left / 7) if left > 0 else math.inf
    return out


def quota_md(q, lang="en"):
    L = [t("q.now", lang, d=q["days_now"], quota=q["quota_usd"]),
         t("q.budget", lang, target=q["target_days"], budget=q["budget_usd_week"], cut=q["cut_usd_week"], p=q["cut_share"])]
    if "days_after_low" in q:
        if abs(q["days_after_low"] - q.get("days_after_high", q["days_after_low"])) < 0.05:
            L.append(t("q.after1", lang, d=q["days_after_low"]))
        else:
            L.append(t("q.after", lang, a=q["days_after_low"], b=q["days_after_high"]))
    return "\n".join(L)


def handover_sweep(ents, pricing, freq_ratio=None, fixed=None, cap=MAX_HANDOVERS_WEEK):
    """Price hand-over thresholds per context. Returns (sweep rows per threshold, chosen per context)."""
    freq_ratio = freq_ratio or {}
    fresh = FRESH_BASE + orgopt.DEFAULT_HANDOVER
    thresholds = (fixed,) if fixed else HANDOVER_THRESHOLDS
    options = {}
    for e in ents:
        c = e["C_p50"]
        # price on the wake-ups that remain after the measured fixes (fewer scheduled fires, no
        # acknowledgement wake-ups), so the savings do not count the same wake-ups twice
        if e["name"] in freq_ratio:
            wpd = e["wakes_per_day"] * freq_ratio[e["name"]]
        else:
            wpd = e["wakes_per_day"] * (1 - e["short_wakes"] / e["wakes"]) if e["wakes"] else 0.0
        for at in thresholds:
            if c <= at or at <= fresh:
                continue
            avg_new = (fresh + at) / 2
            r = orgopt.reset_decision(c, avg_new - orgopt.DEFAULT_HANDOVER, orgopt.DEFAULT_HANDOVER,
                                      wpd, 7, e["rounds_per_wake"], e["cache_hit"], pricing)
            # a context grows per wake-up, so a bot that wakes less often also fills up more slowly
            n = max(1.0, e.get("growth_week", 0.0) * freq_ratio.get(e["name"], 1.0) / (at - fresh))
            net_eq = r["saving_per_wake_eq"] * r["expected_wakes"] - n * r["rebuild_cost_eq"]
            options.setdefault(e["name"], []).append(
                {"name": e["name"], "context": e["unit"], "entity": e, "c": c, "at": at, "avg": avg_new,
                 "handovers_week": n, "usd_week": orgopt.to_usd(net_eq, pricing)})
    sweep = []
    for at in thresholds:
        rows = [o for opts in options.values() for o in opts if o["at"] == at]
        if rows:
            sweep.append({"at": at, "contexts": len(rows), "handovers_week": sum(o["handovers_week"] for o in rows),
                          "usd_week": sum(max(0.0, o["usd_week"]) for o in rows),
                          "max_per_context": max(o["handovers_week"] for o in rows)})
    picks, blocked = [], []
    for name, opts in options.items():
        ok = [o for o in opts if o["usd_week"] > 0 and (fixed or o["handovers_week"] <= cap)]
        if ok:
            picks.append(max(ok, key=lambda o: o["usd_week"]))
        elif any(o["usd_week"] > 0 for o in opts):
            blocked.append(min((o for o in opts if o["usd_week"] > 0), key=lambda o: o["handovers_week"]))
    picks.sort(key=lambda o: -o["usd_week"])
    return sweep, picks, blocked


def _entities(res, att, lang="en"):
    """The contexts to advise on: each unit, except that a bucket split by schedules is replaced by
    its scheduled bots plus what is left (unscheduled traffic)."""
    touched = set(att["touched_units"]) if att else set()
    keys = ("C_p50", "wakes_per_day", "rounds_per_wake", "cache_hit", "wakes", "usd", "short_wakes",
            "read_first_wakes", "fetch_p50", "fetch_p90", "fetch_trend")

    def growth_week(within, wpd, series=0.0):
        # what the wake-ups add themselves, or the measured trend between hand-overs if that is larger
        # (it also counts incoming messages); a trend is only meaningful for a single chat
        return max(within * wpd * 7, series * 7)

    ents = []
    for u in res["units"]:
        if u["unit"] in touched:
            continue
        mixed = u.get("from_model")
        ents.append(dict({k: u[k] for k in keys}, name=u["unit"], unit=u["unit"], share=u["share"],
                         kind="bucket" if mixed else "context", sawtooth=u["sawtooth"],
                         C_floor=u["C_floor"], C_peak=u["C_peak"],
                         growth_week=growth_week(u["growth_per_wake"], u["wakes_per_day"],
                                                 0.0 if mixed else max(u["C_growth_per_day"], 0.0))))
    for b in (att or {}).get("bots", []):
        st = b["stats"]
        if st:
            ents.append(dict({k: st[k] for k in keys}, name=b["name"], unit=b["units"][0], share=b["share"],
                             kind="bot", sawtooth=False, bot=b,
                             growth_week=growth_week(st["growth_per_wake"], st["wakes_per_day"],
                                                     st["series_growth_per_day"])))
    for r in (att or {}).get("remainder", []):
        st = r["stats"]
        ents.append(dict({k: st[k] for k in keys}, name=t("p.other", lang, unit=r["unit"]),
                         unit=r["unit"], share=r["share"], kind="remainder", sawtooth=False,
                         growth_week=growth_week(st["growth_per_wake"], st["wakes_per_day"])))
    ents.sort(key=lambda e: -e["usd"])
    return ents


def plan(res, tr, gap_list, pricing, goal_usd_week=None, handover_at=None, att=None, lang="en",
         max_handovers_week=MAX_HANDOVERS_WEEK, quota_days=None, target_days=7):
    wk = 7 / _rate_days(res)
    week_usd = res["total_usd"] * wk
    quota = None
    if quota_days:
        q = quota_math(week_usd, quota_days, target_days)
        goal_usd_week = q["budget_usd_week"]
        quota = q
    units = res["units"]
    err = res["calibration"]["error"]
    calib_ok = not math.isnan(err) and abs(err) <= CALIB_OK
    thr = pricing.get("long_context_threshold")
    ents = _entities(res, att, lang)
    actions = []

    def add(code, unit, low, high, conf, effort, tier, title_key=None, **kw):
        if high < MIN_SHARE * week_usd:
            return None
        actions.append({"code": code, "unit": unit, "usd_week_low": low, "usd_week_high": high,
                        "confidence": conf, "effort": effort, "tier": tier,
                        "title": t(title_key or f"p.{code}", lang, unit=unit, thr=f"{thr:,.0f}" if thr else "-", **kw),
                        "why": t(f"p.{code}.why", lang, **kw),
                        "how": t(f"p.{code}.how", lang, unit=unit, **kw), "request": None})
        return actions[-1]

    # measured, per scheduled bot: checks that find nothing to do. The cost per wake-up is measured, so
    # fewer fires save that cost times the fires removed (the range allows busy checks to grow a little).
    freq_bots, freq_ratio = set(), {}
    for b in (att or {}).get("bots", []):
        st = b["stats"]
        if not st or st["wakes"] < 10 or st["idle_share"] < FREQ_IDLE:
            continue
        old = b["interval_min"]
        factor = 8.0 if st["idle_share"] >= 1 else min(8.0, 1 / (1 - st["idle_share"]))
        new = schedules.nice_interval(old * factor)
        if new <= old:
            continue
        hi = st["usd_week"] * (1 - old / new)
        a = add("frequency", b["name"], 0.7 * hi, hi, "high" if b["reliable"] else "medium", "low", "measured",
                bot=b["name"], old=b["schedule"], new=schedules.fmt_interval(new, lang),
                n=st["wakes"], idle=st["idle_share"], per=st["per_wake"], c=f"{st['C_p50']:,.0f}", wk=st["usd_week"])
        if a:
            a["request"] = t("p.frequency.req", lang, bot=b["name"], old=b["schedule"],
                             new=f"every {schedules.fmt_interval(new)}")
            a["new_interval_min"] = new
            freq_bots.add(b["name"])
            freq_ratio[b["name"]] = old / new

    for u in units:
        if u["long_context_extra_usd"] > 0:
            x = u["long_context_extra_usd"] * wk
            add("long_context", u["unit"], x, x, "high", "low", "measured",
                n=u["long_context_calls"], usd=u["long_context_extra_usd"])
    total_wakes = sum(u["wakes"] for u in units) or 1
    short = sum(u["short_wakes"] for u in units)
    overlap = sum(b["stats"]["short_usd"] for b in (att or {}).get("bots", []) if b["name"] in freq_bots)
    x = max(0.0, res["anatomy"]["short_wake_usd"] - overlap) * wk     # idle scheduled checks are counted above
    a = add("ack", None, x / 2, x, "medium", "low", "measured",
            n=short, p=short / total_wakes, units=_top_units(units, "short_wake_usd"))
    if a:
        a["request"] = t("p.ack.req", lang)
    rf = sum(u["read_first_wakes"] for u in units)
    x = res["anatomy"]["read_first_usd"] * wk
    a = add("read_first", None, x / 2, x, "medium", "low", "measured",
            n=rf, p=rf / total_wakes, units=_top_units(units, "read_first_usd"))
    if a:
        a["request"] = t("p.read_first.req", lang)

    # modeled from a measured size: read-then-work wake-ups that fetch a lot are reading a whole board.
    # Whatever is fetched stays in the context, so compare reading the whole board with reading only the
    # section needed (after a regular clean-up), under the hand-over policy that applies to that context.
    big = []
    for e in ents:
        if e.get("read_first_wakes", 0) < BOARD_MIN_READS or e.get("fetch_p50", 0) < BOARD_BIG:
            continue
        policy = e.get("C_peak") if e.get("sawtooth") else None      # the platform's own summarizing
        sec = min(e["fetch_p50"], BOARD_SECTION)
        pings = e["read_first_wakes"] / _rate_days(res)
        opt = orgopt.board_options(e["C_p50"], pings, e["fetch_p50"], sec, k=max(e["rounds_per_wake"] - 2, 0),
                                   cache_hit=e["cache_hit"], handover_at=policy, pricing=pricing)
        if opt["saving_section_usd_week"] > 0:
            big.append((e, opt, sec))
    if big:
        big.sort(key=lambda x: -x[1]["saving_section_usd_week"])
        hi = sum(o["saving_section_usd_week"] for _, o, _ in big)
        sep_ = "、" if lang == "zh-TW" else ", "
        items = sep_.join(t("p.board_trim.item", lang, name=e["name"], f=f"{e['fetch_p50']:,.0f}",
                            f90=f"{e['fetch_p90']:,.0f}", n=e["read_first_wakes"] / _rate_days(res))
                          for e, _, _ in big[:3])
        growing = [e for e, _, _ in big if e.get("fetch_trend", 1) >= BOARD_GROWING]
        grow = t("p.board_trim.growing", lang, name=growing[0]["name"], r=growing[0]["fetch_trend"]) if growing else ""
        a = add("board_trim", ", ".join(e["name"] for e, _, _ in big), hi / 2, hi, "medium", "medium", "modeled",
                items=items, sec=f"{BOARD_SECTION:,.0f}", grow=grow)
        if a:
            named = [e["name"] for e, _, _ in big if e["kind"] in ("bot", "context")]
            if any(e["kind"] in ("bucket", "remainder") for e, _, _ in big):
                named.append(t("p.req.board_readers", lang))
            a["request"] = [t("p.board_trim.req_read", lang, names=sep_.join(named)),
                            t("p.board_trim.req_keep", lang, sec=f"{BOARD_SECTION:,.0f}",
                              cap=f"{BOARD_CAP:,.0f}")]
            a["per_unit"] = [{"unit": e["name"], "fetch_p50": e["fetch_p50"], "section": sec,
                              "usd_week": o["saving_section_usd_week"]} for e, o, sec in big]
            # a smaller board also slows the context's growth, so the hand-overs below are priced on it
            for e, _, sec in big:
                e["growth_week"] = max(0.0, e["growth_week"] - (e["fetch_p50"] - sec) * e["read_first_wakes"]
                                       / _rate_days(res) * 7)
                e["board_trimmed"] = True
    # modeled: hand over to a fresh chat whenever a context passes a threshold T. Under that policy a
    # context averages about (fresh start + T) / 2, and it needs (growth per week) / (T - fresh start)
    # hand-overs a week, each costing one rebuild (the verified reset model). Every candidate T is priced
    # for every context, so an impractical threshold never removes the lever: each context gets the
    # threshold that saves most within `max_handovers_week`, unless `handover_at` fixes one for all.
    conf = "medium" if calib_ok else "low"
    sweep, picks, blocked = handover_sweep(ents, pricing, freq_ratio, handover_at, max_handovers_week)
    ho = [p for p in picks if p["usd_week"] > 0]
    if ho:
        hi = sum(p["usd_week"] for p in ho)
        covered = {p["context"] for p in ho}
        absorbed = [a for a in actions if a["code"] == "long_context" and a["unit"] in covered]
        prem = sum(a["usd_week_low"] for a in absorbed)
        sep_ = "、" if lang == "zh-TW" else ", "
        names = sep_.join(t("p.handover.item", lang, name=p["name"], c=f"{p['c']:,.0f}", at=f"{p['at']:,.0f}",
                             n=p["handovers_week"])
                          + (t("p.handover.after_trim", lang) if p["entity"].get("board_trimmed") else "")
                          for p in ho[:4]) + ("…" if len(ho) > 4 else "")
        saw = [p["entity"] for p in ho if p["entity"]["sawtooth"]]
        sawtxt = t("p.handover.saw", lang, floor=f"{saw[0]['C_floor']:,.0f}", peak=f"{saw[0]['C_peak']:,.0f}",
                   unit=saw[0]["name"]) if saw else ""
        if absorbed:
            sawtxt += t("p.handover.absorbed", lang,
                        n=sum(u["long_context_calls"] for u in units if u["unit"] in covered),
                        thr=f"{thr:,.0f}" if thr else "-", usd=prem)
        ats = sorted({p["at"] for p in ho})
        a = add("handover", ", ".join(p["name"] for p in ho), max(hi / 2, prem), max(hi, prem), conf, "low",
                "modeled", title_key="p.handover" if len(ats) == 1 else "p.handover_multi",
                units=names, at=f"{ats[0]:,.0f}", saw=sawtxt, n=sum(p["handovers_week"] for p in ho))
        if a:
            a["per_unit"] = [{"unit": p["name"], "context": p["context"], "usd_week": p["usd_week"], "at": p["at"],
                              "handovers_week": p["handovers_week"]} for p in ho]
            reqs = []
            for at in ats:
                group = [p for p in ho if p["at"] == at]
                named = [p["name"] for p in group if p["entity"]["kind"] in ("bot", "context")]
                if any(p["entity"]["kind"] in ("bucket", "remainder") for p in group):
                    named.append(t("p.req.others", lang))
                reqs.append(t("p.handover.req", lang, names=sep_.join(named), at=f"{at:,.0f}"))
            a["request"] = reqs
            for x in absorbed:
                actions.remove(x)
    plan_handover = {"sweep": sweep, "picks": picks, "blocked": blocked, "max_per_week": max_handovers_week,
                     "fixed": handover_at}

    # cache misses inside a wake-up cost in proportion to the context, so after the hand-overs and
    # frequency changes only the remaining share of input spend is left to recover
    removed = sum(a["usd_week_high"] for a in actions if a["code"] in ("handover", "frequency"))
    left = max(0.0, 1 - removed / week_usd) if week_usd else 1.0
    # acknowledgement and fetch-first calls also re-read the context, so once hand-overs shrink the
    # contexts, what is left of them costs less; scale them the same way (frequency changes have already
    # taken their own idle checks out of the acknowledgement figure)
    if any(a["code"] == "handover" for a in actions):
        ho_removed = sum(a["usd_week_high"] for a in actions if a["code"] == "handover")
        shrink = max(0.0, 1 - ho_removed / week_usd) if week_usd else 1.0
        for a in actions:
            if a["code"] in ("ack", "read_first"):
                a["usd_week_low"] *= shrink
                a["usd_week_high"] *= shrink
    x = res["anatomy"]["in_wake_miss_premium_usd"] * wk * left
    add("cache", None, x / 2, x, "medium", "medium", "measured",
        usd=res["anatomy"]["in_wake_miss_premium_usd"], units=_top_units(units, "in_wake_miss_premium_usd"))

    for a in actions:
        if a["code"] == "long_context":
            a["request"] = t("p.handover.req", lang, names=a["unit"],
                             at=f"{handover_at or max(HANDOVER_THRESHOLDS):,.0f}")

    # the biggest remaining driver (bots whose schedule is already being changed are set aside): a named
    # bot gets a judgment-tier action; a bucket that still mixes several bots, or unscheduled traffic,
    # is the next analysis step, not something to hand back to the client
    rest = [e for e in ents if e["name"] not in freq_bots]
    top = rest[0] if rest else None
    analysis_next = None
    if top and top["kind"] == "bucket" and top["share"] >= SPLIT_SHARE:
        analysis_next = {"kind": "split_bucket", "unit": top["name"], "share": top["share"]}
    elif top and top["kind"] == "remainder" and top["share"] >= SPLIT_SHARE:
        analysis_next = {"kind": "split_remainder", "unit": top["unit"], "share": top["share"],
                         "usd_week": top["usd"] * wk}
    elif top and top["share"] >= HOTSPOT_SHARE:
        x = top["usd"] * wk
        ch = next((c for c in tr["changes"] if c["unit"] == top["name"]), None) if tr else None
        chg = ""
        if ch and ch["wakes_day_before"] and abs(ch["wakes_day_after"] / ch["wakes_day_before"] - 1) >= 0.2:
            chg = t("p.hotspot.chg", lang, a=ch["wakes_day_before"], b=ch["wakes_day_after"])
        add("hotspot", top["name"], 0.1 * x, 0.3 * x, "low", "medium", "judgment",
            p=top["share"], w=top["wakes_per_day"], per=top["usd"] / top["wakes"], chg=chg)

    for a in actions:
        mid = (a["usd_week_low"] + a["usd_week_high"]) / 2
        a["score"] = mid * CONF_W[a["confidence"]] / EFFORT_W[a["effort"]]
    actions.sort(key=lambda a: -a["score"])

    # combined, each dollar once: measured patterns are disjoint (idle scheduled checks are taken out of
    # the acknowledgement figure, the long-context premium is folded into the hand-over); judgment
    # ranges are kept out of the total.
    lo = sum(a["usd_week_low"] for a in actions if a["tier"] != "judgment")
    hi = sum(a["usd_week_high"] for a in actions if a["tier"] != "judgment")
    lo, hi = min(lo, week_usd), min(hi, week_usd)

    goal = None
    if goal_usd_week is not None:
        need = week_usd - goal_usd_week
        goal = {"target": goal_usd_week, "need": need, "met": need <= 0, "reachable": need <= lo,
                "remaining": max(0.0, need - lo)}
        if top and goal["remaining"] > 0 and top["usd"] > 0:
            goal["hotspot_cut"] = goal["remaining"] / (top["usd"] * wk)
            goal["hotspot_unit"] = top["name"]

    # overall confidence
    sev = {g["severity"] for g in gap_list}
    reasons = [g["label"] for g in gap_list if g["severity"] == "high"]
    if "high" in sev:
        conf = "low"
    elif not calib_ok or "medium" in sev:
        conf = "medium"
    else:
        conf = "high"
    return {"usd_per_week": week_usd, "actions": actions, "combined_low": lo, "combined_high": hi,
            "goal": goal, "confidence": conf, "confidence_gaps": reasons, "calibration_ok": calib_ok,
            "handover_at": handover_at, "handover": plan_handover, "entities": ents,
            "analysis_next": analysis_next,
            "quota": dict(quota, **quota_math(week_usd, quota_days, target_days, lo, hi)) if quota else None}


def kpis(res, pl, pricing, tz=timezone.utc, lang="en"):
    wk = 7 / _rate_days(res)
    units = res["units"]
    end = datetime.fromisoformat(res["period_end"]).astimezone(tz).date()
    check = (end + timedelta(days=7)).isoformat()
    total_wakes = sum(u["wakes"] for u in units) or 1
    inp = sum(u["input_tokens"] for u in units)
    cache = sum(u["cache_hit"] * u["input_tokens"] for u in units) / inp if inp else 0.0
    target_week = pl["goal"]["target"] if pl["goal"] else pl["usd_per_week"] - pl["combined_low"]
    out = [{"name": t("m.k_week", lang), "now": f"${pl['usd_per_week']:,.0f}", "target": f"≤ ${target_week:,.0f}", "check": check}]
    if pl.get("quota"):
        q = pl["quota"]
        out.append({"name": t("m.k_quota", lang), "now": t("m.days", lang, d=q["days_now"]),
                    "target": "≥ " + t("m.days", lang, d=q["target_days"]), "check": check})
    short = sum(u["short_wakes"] for u in units) / total_wakes
    if short >= 0.05:
        out.append({"name": t("m.k_ack", lang), "now": f"{short:.0%}", "target": "≤ 5%", "check": check})
    rf = sum(u["read_first_wakes"] for u in units) / total_wakes
    if rf >= 0.10:
        out.append({"name": t("m.k_read", lang), "now": f"{rf:.0%}", "target": "≤ 10%", "check": check})
    out.append({"name": t("m.k_cache", lang), "now": f"{cache:.0%}",
                "target": f"≥ {max(cache, 0.8):.0%}" if cache < 0.8 else f"≥ {cache:.0%}", "check": check})
    long_calls = sum(u["long_context_calls"] for u in units) * wk
    thr = pricing.get("long_context_threshold")
    if long_calls:
        out.append({"name": t("m.k_long", lang), "now": f"{long_calls:,.0f}", "target": "0", "check": check})
    if units and thr:
        u = units[0]
        ho = next((a for a in pl["actions"] if a["code"] == "handover"), None)
        ats = [p["at"] for p in (ho or {}).get("per_unit", []) if p["context"] == u["unit"]]
        if ats:
            target = max(ats)
        else:
            target = min(thr, 5000 * math.ceil(max(u["C_p90"] * 0.6, 3 * (FRESH_BASE + orgopt.DEFAULT_HANDOVER)) / 5000))
        out.append({"name": t("m.k_ctx", lang, unit=u["unit"]), "now": f"{u['C_p90']:,.0f}",
                    "target": f"≤ {target:,.0f}", "check": check})
    return out


# ---------------------------------------------------------------- report / memo
def _infer_quota_days(cycles):
    """Latest complete cycle between real gaps.

    The cycle before the first gap starts at the export boundary and is partial, so it is
    not a measured quota. Quota days are inferred only once a later gap closes a full cycle.
    """
    complete = [c for c in cycles if not c["partial"] and c["days"] > 0]
    return complete[-1] if complete else None


def report(csv_path, pricing, overrides=None, cached_separate=False, gap=120, goal_usd_week=None,
           tz=timezone.utc, handover_at=None, schedule_specs=None, lang="en",
           max_handovers_week=MAX_HANDOVERS_WEEK, quota_days=None, target_days=7,
           max_gap_hours=None):
    """Everything the memo needs. `schedule_specs` (from schedules.load) splits buckets into bots."""
    if max_gap_hours is None:
        max_gap_hours = analyze_usage.DEFAULT_MAX_GAP_HOURS
    info = {}
    rows, mapping, skipped = analyze_usage.load_rows(csv_path, overrides, cached_separate, lang, info)
    res = analyze_usage.analyze(rows, pricing, gap, has_cost="cost" in mapping, lang=lang,
                                max_gap_hours=max_gap_hours)
    res["column_mapping"], res["skipped_rows"], res["load_info"] = mapping, skipped, info
    from_model = {r["unit"] for r in rows if r.get("bot_from_model")}
    for u in res["units"]:
        u["from_model"] = u["unit"] in from_model
    tr = trend(rows, pricing, "cost" in mapping, tz, max_gap_hours)
    gl = gaps(res, mapping, info, lang)
    att = attribute(rows, res, schedule_specs, pricing, "cost" in mapping, tz, gap) if schedule_specs else None
    if att:
        for g in gl:
            if g["code"] in ("bot_empty", "no_bot"):
                g["detail"] += t("g.att", lang, p=att["matched_share"], n=sum(1 for b in att["bots"] if b["stats"]))
                if att["matched_share"] >= 0.3 and g["severity"] == "high":
                    g["severity"] = "medium"
    inferred = None
    quota_omitted = None
    if quota_days is None:
        inferred = _infer_quota_days(res.get("quota_cycles") or [])
        if inferred:
            inferred = dict(inferred, days=round(inferred["days"], 1))
            quota_days = inferred["days"]
        elif not res.get("usage_gaps"):
            quota_omitted = "no_gap"
    pl = plan(res, tr, gl, pricing, goal_usd_week, handover_at, att, lang, max_handovers_week, quota_days, target_days)
    return {"analysis": res, "trend": tr, "gaps": gl, "plan": pl, "attribution": att,
            "quota_inferred": inferred, "quota_omitted": quota_omitted,
            "kpis": kpis(res, pl, pricing, tz, lang),
            "pricing": {"model": pricing.get("model", "?"), **pricing["per_million"],
                        "long_context_threshold": pricing.get("long_context_threshold")},
            "gap_seconds": gap, "tz": tz}


def org_skeleton(rep, horizon_days=7):
    """org.json skeleton from the report's contexts (named bots included): the advisor adds who talks to
    whom, moves the wake-ups those messages cause out of own_wakes_per_day, and marks bots to keep."""
    bots = []
    for e in rep["plan"]["entities"]:
        name = e["name"].replace(" ", "_").replace("（", "(").replace("）", ")")
        bots.append({"name": name, "context_tokens": round(e["C_p50"]),
                     "base_tokens": round(min(e["C_p50"], FRESH_BASE)),
                     "own_wakes_per_day": round(e["wakes_per_day"], 2),
                     "work_rounds_per_wake": round(max(e["rounds_per_wake"] - 1, 0), 2),
                     "cache_hit_ratio": round(e["cache_hit"], 3)})
    return {"horizon_days": horizon_days, "_note": t("u.skeleton_note", "en"), "bots": bots, "links": [], "groups": []}


def _usd(x):
    return f"${x:,.0f}" if abs(x) >= 100 else f"${x:,.2f}"


def _local_stamp(iso, tz):
    return datetime.fromisoformat(iso).astimezone(tz).strftime("%Y-%m-%d %H:%M")


def memo_md(rep, lang="en"):
    res, pl, tr = rep["analysis"], rep["plan"], rep["trend"]
    wk = 7 / _rate_days(res)
    units = res["units"]
    an = res["anatomy"]
    zh = lang == "zh-TW"
    tz = rep.get("tz") or timezone.utc
    local = lambda iso: datetime.fromisoformat(iso).astimezone(tz)
    L = [f"# {t('m.title', lang, start=local(res['period_start']).date(), end=local(res['period_end']).date())}", ""]

    # bottom line
    L += [f"## {t('m.bottom', lang)}", ""]
    eq = t("m.b1_eq", lang) if (rep["analysis"]["load_info"].get("cost_text_rows") or not res.get("has_cost")) else ""
    L.append("1. " + t("m.b1", lang, wk=pl["usd_per_week"], day=pl["usd_per_week"] / 7, eq=eq)
             + (t("m.b1_quota", lang, now=pl["quota"]["days_now"], target=pl["quota"]["target_days"],
                  budget=pl["quota"]["budget_usd_week"], cut=pl["quota"]["cut_share"]) if pl.get("quota") else ""))
    if units:
        hot = units[0]
        priced = an["uncached_input_usd"] + an["cached_input_usd"] + an["output_usd"]
        reread = (an["uncached_input_usd"] + an["cached_input_usd"]) / priced if priced else 0.0
        att = rep.get("attribution")
        inside = ""
        if att and hot["unit"] in att["touched_units"]:
            top_bot = next((e for e in pl["entities"] if e["kind"] == "bot" and e["unit"] == hot["unit"]), None)
            if top_bot:
                inside = t("m.b2_bot", lang, bot=top_bot["name"], p=top_bot["share"], sched=top_bot["bot"]["schedule"])
        L.append("2. " + t("m.b2", lang, p=hot["share"], unit=hot["unit"],
                           bucket=t("m.b2_bucket", lang) if hot.get("from_model") and not inside else "",
                           reread=reread) + inside)
    g = pl["goal"]
    goal_txt = ""
    if g:
        if g["met"]:
            goal_txt = t("m.goal_met", lang, goal=g["target"])
        elif g["reachable"]:
            goal_txt = t("m.goal_ok", lang, goal=g["target"])
        elif g.get("hotspot_cut") is not None and g["hotspot_cut"] <= 1:
            goal_txt = t("m.goal_gap", lang, goal=g["target"], gap=g["remaining"], cut=g["hotspot_cut"], unit=g["hotspot_unit"])
        else:
            goal_txt = t("m.goal_gap_nounit", lang, goal=g["target"], gap=g["remaining"])
    if pl.get("quota") and pl["combined_high"] > 0:
        q = pl["quota"]
        goal_txt = t("m.quota_after", lang, a=q["days_after_low"], b=q["days_after_high"], target=q["target_days"])
    if pl["combined_high"] > 0:
        w = pl["usd_per_week"] or 1
        L.append("3. " + t("m.b3", lang, lo=pl["combined_low"], hi=pl["combined_high"],
                           plo=pl["combined_low"] / w, phi=pl["combined_high"] / w, goal=goal_txt))
    else:
        L.append("3. " + t("m.b3_none", lang) + (goal_txt.lstrip("；; ") if goal_txt else ""))
    why = []
    if pl["confidence_gaps"]:
        why.append(t("m.conf_why_gaps", lang, g=", ".join(pl["confidence_gaps"])))
    if not pl["calibration_ok"] and not math.isnan(res["calibration"]["error"]):
        why.append(t("m.conf_why_calib", lang, err=res["calibration"]["error"]))
    L += ["", t("m.conf", lang, c=t(f"m.lvl.{pl['confidence']}", lang),
               why=t("m.conf_why", lang, why=("；" if zh else "; ").join(why)) if why else ""), ""]

    if rep.get("quota_omitted") == "no_gap":
        L += [t("m.quota_omitted", lang), ""]
    if res.get("usage_gaps"):
        L += [f"### {t('m.quota_gap', lang)}", ""]
        for g in res["usage_gaps"]:
            L.append("- " + t("m.usage_gap", lang, start=_local_stamp(g["start"], tz),
                              end=_local_stamp(g["end"], tz), days=g["days"]))
        L += ["", t("m.denominator", lang, active=res["active_days"], span=res["span_days"],
                    gap=sum(g["days"] for g in res["usage_gaps"])), ""]
        for c in res.get("quota_cycles") or []:
            L.append("- " + t("m.cycle", lang, start=_local_stamp(c["start"], tz), end=_local_stamp(c["end"], tz),
                              days=c["days"], usd=c["usd"], tokens=c["tokens"],
                              partial=t("m.cycle_partial", lang) if c["partial"] else ""))
        inferred = rep.get("quota_inferred")
        if inferred:
            L += ["", t("m.cycle_inferred", lang, days=inferred["days"])]
        L.append("")

    # where the money goes
    L += [f"## {t('m.where', lang)}", "", t("m.where_header", lang), "|---|---|---|---|---|---|"]
    for u in units[:3]:
        L.append(f"| {u['unit']} | {_usd(u['usd'] * wk)} | {u['share']:.0%} | {u['wakes_per_day']:.1f} | "
                 f"${u['usd'] / u['wakes']:,.3f} | {u['C_p50']:,.0f} / {u['C_p90']:,.0f} |")
    if len(units) > 3:
        rest = units[3:]
        usd = sum(u["usd"] for u in rest)
        L.append(f"| {t('m.others', lang, n=len(rest))} | {_usd(usd * wk)} | {sum(u['share'] for u in rest):.0%} | "
                 f"{sum(u['wakes_per_day'] for u in rest):.1f} | | |")
    att = rep.get("attribution")
    if att and att["bots"]:
        L += ["", f"### {t('m.inside', lang, units=', '.join(att['touched_units']))}", "",
              t("m.inside_header", lang), "|---|---|---|---|---|---|---|---|"]
        for b in att["bots"]:
            st = b["stats"]
            if not st:
                L.append(f"| {b['name']} | `{b['schedule']}` | 0 / {b['fires']} | | | | | |")
                continue
            L.append(f"| {b['name']} | `{b['schedule']}` | {b['matched']} / {b['fires']} | {_usd(st['usd_week'])} | "
                     f"{b['share']:.0%} | ${st['per_wake']:,.3f} | {st['C_p50']:,.0f} | {st['idle_share']:.0%} |")
        for r in att["remainder"]:
            st = r["stats"]
            L.append(f"| {t('p.other', lang, unit=r['unit'])} | {t('m.unscheduled', lang)} | {st['wakes']} | "
                     f"{_usd(st['usd_week'])} | {r['share']:.0%} | ${st['per_wake']:,.3f} | {st['C_p50']:,.0f} | "
                     f"{st['idle_share']:.0%} |")
        weak = [b["name"] for b in att["bots"] if not b["reliable"]]
        L += ["", t("m.inside_note", lang, w=att["window_s"] // 60, idle=IDLE_OUTPUT)
              + (t("m.inside_weak", lang, bots=", ".join(weak)) if weak else "")]
    priced = an["uncached_input_usd"] + an["cached_input_usd"] + an["output_usd"] or 1
    L += ["", f"### {t('m.anatomy', lang)}", "", t("m.an_header", lang), "|---|---|---|---|"]
    for key, label in (("uncached_input_usd", "m.an_unc"), ("cached_input_usd", "m.an_cached"),
                       ("output_usd", "m.an_out"), ("long_premium_usd", "m.an_long")):
        if key == "long_premium_usd" and not an[key]:
            continue
        L.append(f"| {t(label, lang)} | {_usd(an[key] * wk)} | {an[key] / priced:.0%} | {t(label + '.d', lang)} |")

    changed = []
    s = tr["same_window"]
    if s and not math.isnan(s["change"]):
        changed.append(t("tr.same", lang, day=s["day"], until=s["until"], a=s["today_usd"], b=s["yesterday_usd"], chg=s["change"])
                       + (t("tr.same_partial", lang) if s["yesterday_partial"] else ""))
    for c in tr["changes"][:2]:
        if c["usd_day_before"] and abs(c["usd_day_after"] / c["usd_day_before"] - 1) < 0.2:
            continue
        changed.append(t("m.change_line", lang, unit=c["unit"], a=c["usd_day_before"], b=c["usd_day_after"],
                         wa=c["wakes_day_before"], wb=c["wakes_day_after"],
                         ca=f"{c['context_before']:,.0f}", cb=f"{c['context_after']:,.0f}"))
    if changed:
        L += ["", f"### {t('m.changed', lang)}", ""] + [f"- {x}" for x in changed]

    # recommendations
    L += ["", f"## {t('m.recs', lang)}", ""]
    if pl["actions"]:
        L += [t("m.recs_note", lang), "", t("m.rec_header", lang), "|---|---|---|---|---|---|---|"]
        for i, a in enumerate(pl["actions"], 1):
            rng = _usd(a["usd_week_low"]) if abs(a["usd_week_high"] - a["usd_week_low"]) < 0.005 else \
                f"{_usd(a['usd_week_low'])}–{_usd(a['usd_week_high'])}"
            tier = f"（{t('m.tier.' + a['tier'], lang)}）" if zh else f" ({t('m.tier.' + a['tier'], lang)})"
            L.append(f"| {i} | **{a['title']}** | {a['why']} | {rng}{tier} | "
                     f"{t('m.lvl.' + a['confidence'], lang)} | {t('m.lvl.' + a['effort'], lang)} | {t('m.owner', lang)} |")
        L += ["", f"**{t('m.how', lang)}**", ""]
        for i, a in enumerate(pl["actions"], 1):
            L.append(f"{i}. {a['how']}")
        hv = pl.get("handover") or {}
        if hv.get("sweep"):
            chosen = {p["at"] for p in hv["picks"] if p["usd_week"] > 0}
            L += ["", f"### {t('m.ho_title', lang)}", "", t("m.ho_note", lang, cap=hv["max_per_week"]), "",
                  t("m.ho_header", lang), "|---|---|---|---|---|"]
            for row in hv["sweep"]:
                mark = " ✓" if row["at"] in chosen else ""
                L.append(f"| {row['at']:,.0f}{mark} | {row['contexts']} | {row['handovers_week']:.1f} | "
                         f"{row['max_per_context']:.1f} | {_usd(row['usd_week'])} |")
        reqs = []
        for a in pl["actions"]:
            r = a.get("request")
            reqs += r if isinstance(r, list) else ([r] if r else [])
        if reqs:
            L += ["", f"### {t('m.paste', lang)}", "", t("m.paste_note", lang), "", "```text", t("m.paste_intro", lang)]
            L += [f"{i}. {r}" for i, r in enumerate(reqs, 1)]
            L += [t("m.paste_outro", lang), "```"]
    else:
        L.append(t("m.none_rec", lang))

    # decisions and data needed
    L += ["", f"## {t('m.decisions', lang)}", ""]
    dec = []
    nxt = pl.get("analysis_next")
    freq = {a["unit"] for a in pl["actions"] if a["code"] == "frequency"}
    top = next((e for e in pl["entities"] if e["name"] not in freq), None)
    if nxt and nxt["kind"] == "split_bucket":
        dec.append(t("m.split_bucket", lang, unit=nxt["unit"], p=nxt["share"]))
    elif nxt and nxt["kind"] == "split_remainder":
        dec.append(t("m.split_remainder", lang, unit=nxt["unit"], p=nxt["share"], usd=nxt["usd_week"]))
    elif top and top["share"] >= HOTSPOT_SHARE:
        dec.append(t("m.decide_hot", lang, unit=top["name"]))
    g = pl["goal"]
    if g and not g["met"] and not g["reachable"]:
        if g.get("hotspot_cut") is not None and g["hotspot_cut"] <= 1:
            dec.append(t("m.decide_goal", lang, goal=g["target"], gap=g["remaining"], unit=g["hotspot_unit"], cut=g["hotspot_cut"]))
        else:
            dec.append(t("m.decide_goal_nounit", lang, goal=g["target"], gap=g["remaining"]))
    hv = pl.get("handover") or {}
    for b in hv.get("blocked", []):
        dec.append(t("m.ho_blocked", lang, name=b["name"], cap=hv["max_per_week"], at=f"{b['at']:,.0f}",
                     n=b["handovers_week"], usd=b["usd_week"])
                   + (t("m.ho_mixed", lang) if b["entity"]["kind"] in ("bucket", "remainder") else ""))
    if hv.get("sweep") and not hv.get("blocked") and not any(a["code"] == "handover" for a in pl["actions"]):
        best = max(hv["sweep"], key=lambda r: r["usd_week"])
        dec.append(t("m.ho_infeasible", lang, cap=hv["max_per_week"], at=f"{best['at']:,.0f}",
                     n=best["max_per_context"], usd=best["usd_week"]))
    colon = "：" if zh else ": "
    for gp in rep["gaps"]:
        dec.append(f"{gp['detail']}{'' if zh else ' '}**{t('m.how', lang)}{colon.strip()}**{'' if zh else ' '}{gp['fix']}")
    L += [f"- {d}" for d in dec] if dec else [t("m.dec_none", lang)]

    # KPIs
    L += ["", f"## {t('m.kpis', lang)}", "", t("m.kpi_header", lang), "|---|---|---|---|"]
    for k in rep["kpis"]:
        L.append(f"| {k['name']} | {k['now']} | {k['target']} | {k['check']} |")
    L += ["", t("m.k_rerun", lang)]

    # appendix
    c = res["calibration"]
    p = rep["pricing"]
    thr = p.get("long_context_threshold")
    L += ["", "---", "", f"### {t('m.appendix', lang)}", ""]
    if res.get("usage_gaps"):
        L.append("- " + t("m.ap_period_active", lang, start=local(res["period_start"]).strftime("%Y-%m-%d %H:%M"),
                          end=local(res["period_end"]).strftime("%Y-%m-%d %H:%M") + f" ({rep['trend']['tz']})",
                          days=res["span_days"], rows=res["rows"], active=res["active_days"],
                          gap=sum(g["days"] for g in res["usage_gaps"])))
    else:
        L.append("- " + t("m.ap_period", lang, start=local(res["period_start"]).strftime("%Y-%m-%d %H:%M"),
                          end=local(res["period_end"]).strftime("%Y-%m-%d %H:%M") + f" ({rep['trend']['tz']})",
                          days=res["span_days"], rows=res["rows"]))
    L += [
          "- " + t("m.ap_pricing", lang, model=p["model"], i=p["input"], c=p["cached_input"], o=p["output"],
                   thr=f"{thr:,}" if thr else "-"),
          "- " + t("m.ap_tiers", lang),
          "- " + t("m.ap_calib", lang, m=c["model_usd"], a=c["actual_usd"], err=c["error"]),
          "- " + t("m.ap_wake", lang, gap=rep["gap_seconds"]),
          *(["- " + t("m.ap_match", lang, early=att["early_s"], w=att["window_s"], idle=IDLE_OUTPUT)] if att else []),
          "- " + t("m.ap_map", lang, m=", ".join(f"{k}={v}" for k, v in res["column_mapping"].items())),
          "- " + t("m.ap_detail", lang)]
    return "\n".join(L) + "\n"
