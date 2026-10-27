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
from datetime import datetime, timedelta, timezone

try:
    from . import orgopt
    from .i18n import t
except ImportError:          # run as a plain script
    import orgopt
    from i18n import t

ALIASES = {
    "time": ["timestamp", "time", "datetime", "date", "created_at", "created", "request_time", "start_time"],
    "bot": ["bot", "bot_name", "botname", "agent", "agent_name", "bot_id", "agent_id", "assistant", "name",
            "automation_id"],
    "chat": ["chat_id", "chat", "thread_id", "thread", "conversation_id", "session_id", "conversation"],
    "input": ["input_tokens", "prompt_tokens", "input", "total_input_tokens", "tokens_in"],
    "cached": ["cached_input_tokens", "cached_tokens", "cache_read_tokens", "cached_prompt_tokens",
               "cache_read_input_tokens", "cached"],
    "output": ["output_tokens", "completion_tokens", "output", "tokens_out"],
    "reasoning": ["reasoning_tokens", "thinking_tokens", "reasoning"],
    "cost": ["cost_usd", "cost", "price_usd", "amount_usd"],
    "model": ["model", "model_name", "model_id"],
}
REQUIRED = ["time", "input"]
# Grok Bot usage-events export (Date, Cloud Agent ID, Automation ID, Kind, Model, Max Mode,
# Input (w/ Cache Write), Input (w/o Cache Write), Cache Read, Output Tokens, Total Tokens, Cost):
# the prompt is split over three columns, so input = their sum and cached = Cache Read.
SPLIT_INPUT = ("Input (w/ Cache Write)", "Input (w/o Cache Write)", "Cache Read")
SPLIT_LABEL = " + ".join(SPLIT_INPUT)
CLOUD_AGENT_COL = "Cloud Agent ID"   # rows with an id here are one-shot cloud runs, not persistent bots
RESET_DROP = 0.6        # a wake-up whose context is < 60% of the previous one ...
RESET_MIN = 5000        # ... and at least 5K tokens smaller counts as a hand-over
DEFAULT_MAX_GAP_HOURS = 12.0   # shorter silences are never quota gaps; longer ones still have to break the export's own clock
EXPECTED_ACTIVE_HOURS = 3       # hours of predicted activity inside a silence before it counts as exhaustion


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
    if "input" not in mapping and "input" not in (overrides or {}) and all(c in header for c in SPLIT_INPUT):
        mapping["input"] = SPLIT_LABEL
        mapping.setdefault("cached", "Cache Read")
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


def load_rows(path, overrides=None, cached_separate=False, lang="en", info=None):
    """Rows sorted by time, the column mapping and the number of skipped rows.
    `info` (a dict), when given, receives load notes: cloud_agent_rows, cost_text_rows, split_input,
    bot_empty_rows (rows whose bot cell is blank; grouped by model when there is a model column),
    bot_column_empty / chat_column_empty (the mapped column is blank on every row)."""
    info = {} if info is None else info
    info.update(cloud_agent_rows=0, cost_text_rows=0, split_input=False, bot_empty_rows=0)
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(8192)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        mapping = map_columns(reader.fieldnames or [], overrides, lang)
        split = mapping["input"] == SPLIT_LABEL
        info["split_input"] = split
        skip_cloud = split and CLOUD_AGENT_COL in (reader.fieldnames or [])
        rows, skipped = [], 0
        for raw in reader:
            if skip_cloud and (raw.get(CLOUD_AGENT_COL) or "").strip():
                info["cloud_agent_rows"] += 1
                continue
            try:
                inp = sum(_num(raw[c]) for c in SPLIT_INPUT) if split else _num(raw[mapping["input"]])
                r = {"time": parse_time(raw[mapping["time"]]), "input": inp}
            except (ValueError, KeyError):
                skipped += 1
                continue
            r["bot"] = (raw.get(mapping["bot"]) or "").strip() if "bot" in mapping else ""
            r["chat"] = (raw.get(mapping["chat"]) or "").strip() if "chat" in mapping else ""
            r["model"] = (raw.get(mapping["model"]) or "").strip() if "model" in mapping else ""
            if "bot" in mapping and not r["bot"]:
                info["bot_empty_rows"] += 1
                r["bot"] = r["model"]        # e.g. Grok Bot: no Automation ID -> group by model
                r["bot_from_model"] = bool(r["model"])
            for k in ("cached", "output", "reasoning", "cost"):
                try:
                    r[k] = _num(raw.get(mapping[k], 0)) if k in mapping else 0.0
                except ValueError:
                    if k != "cost":
                        raise UsageError(t("u.err_num", lang, col=mapping[k], v=raw.get(mapping[k])))
                    info["cost_text_rows"] += 1      # e.g. "Included" / "Free" on plan-billed exports
                    r[k] = 0.0
            if cached_separate and not split:    # cached tokens reported separately from input
                r["input"] += r["cached"]
            r["cached"] = min(r["cached"], r["input"])
            rows.append(r)
    if info["cost_text_rows"]:
        # a cost column that is not a number (plan-billed) cannot calibrate anything: price the calls instead
        info["cost_column"] = mapping.pop("cost")
        for r in rows:
            r["cost"] = 0.0
    rows.sort(key=lambda r: r["time"])
    if rows and "bot" in mapping and info["bot_empty_rows"] == len(rows):
        info["bot_column_empty"] = mapping["bot"]
    if rows and "chat" in mapping and not any(r["chat"] for r in rows):
        info["chat_column_empty"] = mapping["chat"]
    info["rows_loaded"] = len(rows)
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


def percentile(xs, q):
    """Linear-interpolated percentile (q in 0..1) of a non-empty list."""
    v = sorted(xs)
    if len(v) == 1:
        return float(v[0])
    pos = q * (len(v) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def cost_parts(r, pricing):
    """(uncached input, cached input, output incl. reasoning, long-context premium) in USD for one call."""
    thr = pricing.get("long_context_threshold")
    base = pricing["per_million"]
    long_tier = bool(thr and r["input"] >= thr and pricing.get("long_context_per_million"))
    table = pricing["long_context_per_million"] if long_tier else base
    cached = min(r["cached"], r["input"])
    unc = r["input"] - cached
    out = r["output"] + r.get("reasoning", 0.0)
    parts = (unc * table["input"] / 1e6, cached * table["cached_input"] / 1e6, out * table["output"] / 1e6)
    premium = 0.0
    if long_tier:
        premium = sum(parts) - (unc * base["input"] + cached * base["cached_input"] + out * base["output"]) / 1e6
    return parts + (premium,)


def miss_premium(r, pricing):
    """What the uncached part of one call cost above the cached price."""
    thr = pricing.get("long_context_threshold")
    table = pricing.get("long_context_per_million") if thr and r["input"] >= thr else None
    table = table or pricing["per_million"]
    return (r["input"] - min(r["cached"], r["input"])) * (table["input"] - table["cached_input"]) / 1e6


def wake_growth(wake):
    """Tokens a wake-up adds to its context: what the calls after the first one read on top of it, plus
    the last answer. Robust when several chats share a bucket, because it never compares two wake-ups."""
    last = wake[-1]
    return max(0.0, last["input"] + last["output"] + last.get("reasoning", 0.0) - wake[0]["input"])


def fetched_tokens(wake):
    """In a read-then-work wake-up, what the first (fetching) call brought into the context: the second
    call's prompt minus the first call's prompt and answer. For a board read, this is the board's size."""
    a, b = wake[0], wake[1]
    return max(0.0, b["input"] - a["input"] - a["output"] - a.get("reasoning", 0.0))


def fetch_stats(read_first_wakes):
    """Median and p90 of what read-then-work wake-ups fetch, and how the median moved (second half of the
    wake-ups vs the first half; >1 means the board is growing)."""
    f = [fetched_tokens(w) for w in read_first_wakes]
    if not f:
        return {"fetch_p50": 0.0, "fetch_p90": 0.0, "fetch_trend": 1.0}
    half = len(f) // 2
    early, late = (f[:half], f[half:]) if half >= 3 else (f, f)
    m0 = statistics.median(early)
    return {"fetch_p50": percentile(f, 0.5), "fetch_p90": percentile(f, 0.9),
            "fetch_trend": statistics.median(late) / m0 if m0 else 1.0}


def _hour_dates(local_times):
    """Dates that have a call in each hour-of-day, on the clock `local_times` already use."""
    by_hour = [set() for _ in range(24)]
    for t in local_times:
        by_hour[t.hour].add(t.date())
    return by_hour


def _busy_hours(by_hour):
    """Hours with calls on at least half as many dates as the busiest hour."""
    peak = max(len(s) for s in by_hour)
    if peak == 0:
        return set()
    need = max(1, math.ceil(peak * 0.5))
    return {h for h, s in enumerate(by_hour) if len(s) >= need}


def _longest_quiet_run(busy):
    """(start hour, length) of the longest cyclic run of hours that are not busy.

    Ties keep the first run that starts after the earliest busy hour. None when every hour is busy."""
    if len(busy) >= 24:
        return None
    quiet = [h not in busy for h in range(24)]
    if not any(quiet):
        return None
    if all(quiet):
        return 0, 24
    origin = next(h for h in range(24) if not quiet[h])
    rotated = quiet[origin:] + quiet[:origin]
    best_len, best_start, i = 0, 0, 0
    while i < 24:
        if not rotated[i]:
            i += 1
            continue
        j = i
        while j < 24 and rotated[j]:
            j += 1
        length = j - i
        if length > best_len:
            best_len = length
            best_start = (origin + i) % 24
        i = j
    return best_start, best_len


def _offset_tz(boundary_hour):
    """Fixed offset whose local midnight is `boundary_hour` UTC, wrapped to −11…+12."""
    offset = (-boundary_hour) % 24
    if offset > 12:
        offset -= 24
    if offset == 0:
        return timezone.utc
    return timezone(timedelta(hours=offset))


def _quiet_point_tz(rows):
    """Frame for an export with no `--tz`: the middle of the longest UTC quiet run.

    A 24 h bot (every hour busy) stays on UTC. The boundary is `start + length // 2`."""
    utc = [r["time"].astimezone(timezone.utc) for r in rows]
    run = _longest_quiet_run(_busy_hours(_hour_dates(utc)))
    if run is None:
        return timezone.utc
    start, length = run
    return _offset_tz((start + length // 2) % 24)


def _covered_weekdays(local):
    """Dates with rows, and full dates, both grouped by weekday.

    A full date is a frame-date strictly after the first row's date and strictly before the last.
    Empty means that date is not in the row-date set."""
    on_dates = [set() for _ in range(7)]
    for t in local:
        on_dates[t.weekday()].add(t.date())
    first, last = local[0].date(), local[-1].date()
    covered = [[] for _ in range(7)]
    day = first + timedelta(days=1)
    while day < last:
        covered[day.weekday()].append(day)
        day += timedelta(days=1)
    return on_dates, covered


def _rest_weekdays(on_dates, covered):
    """(rest weekdays, judged).

    Rest days are judged only when every weekday has at least 2 full dates. A judged weekday
    is rest when every one of those dates is empty. Any set of weekdays can be rest; they do
    not have to be adjacent. When some weekday has fewer than 2 full dates, nothing is rest."""
    if any(len(days) < 2 for days in covered):
        return set(), False
    rest = set()
    for weekday, days in enumerate(covered):
        if days and all(d not in on_dates[weekday] for d in days):
            rest.add(weekday)
    return rest, True


def _activity_clock(rows, tz):
    """Busy hours and rest weekdays, both on `tz`.

    An hour-of-day is busy when it has calls on at least half as many dates as the busiest hour.
    A full date is a frame-date strictly after the first row's date and strictly before the last.
    A weekday is rest when it has at least 2 full dates and every one of them is empty.
    When some weekday has fewer than 2 full dates, rest days are not judged and the rest set is empty."""
    local = [r["time"].astimezone(tz) for r in rows]
    active_hours = _busy_hours(_hour_dates(local))
    rest, _judged = _rest_weekdays(*_covered_weekdays(local))
    return active_hours, rest


def _expected_active_hours(start, end, active_hours, weekly_off, tz, row_dates=None):
    """Full frame-hours inside (start, end) where this export's own pattern says a call belongs.

    A date with no rows still counts, unless its weekday is rest. That separates a weekend
    from a day the quota wiped out. When `row_dates` is set, rest days were not judged, so
    only busy hours on dates that have rows count: a silence counts only when it starts or
    ends inside busy hours."""
    local_end = end.astimezone(tz)
    t = start.astimezone(tz).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    step = timedelta(hours=1)
    n = 0
    while t + step <= local_end:
        if t.hour in active_hours:
            if row_dates is not None:
                if t.date() in row_dates:
                    n += 1
            elif t.weekday() not in weekly_off:
                n += 1
        t += step
    return n


def _judge_gaps(rows, max_gap_hours, tz):
    """(gaps, rest_days_judged, rest_days_warning).

    `rest_days_warning` is set when rest days were not judged, the export covers at least
    3 frame-dates, and at least one silence is longer than `max_gap_hours`. Fewer than 3
    frame-dates still yield no gaps and no warning."""
    if max_gap_hours < 0:
        raise ValueError("max-gap must be >= 0 hours")
    if len(rows) < 2:
        return [], False, False
    limit = max_gap_hours * 3600
    candidates = []
    for a, b in zip(rows, rows[1:]):
        seconds = (b["time"] - a["time"]).total_seconds()
        if seconds > limit:
            candidates.append({"start": a["time"], "end": b["time"], "seconds": seconds, "days": seconds / 86400})
    frame = tz if tz is not None else _quiet_point_tz(rows)
    local = [r["time"].astimezone(frame) for r in rows]
    rest, judged = _rest_weekdays(*_covered_weekdays(local))
    if len({t.date() for t in local}) < 3 or not candidates:
        return [], judged, False
    active_hours = _busy_hours(_hour_dates(local))
    row_dates = None if judged else {t.date() for t in local}
    gaps = [g for g in candidates
            if _expected_active_hours(g["start"], g["end"], active_hours, rest, frame, row_dates)
            >= EXPECTED_ACTIVE_HOURS]
    return gaps, judged, not judged


def detect_usage_gaps(rows, max_gap_hours=DEFAULT_MAX_GAP_HOURS, tz=None):
    """Zero-row stretches that are quota exhaustion, not the export's own idle time.

    A silence longer than `max_gap_hours` is only a candidate. Days and hours use `tz`
    when it is given, otherwise the quiet-point frame (`_quiet_point_tz`). The silence
    counts when at least `EXPECTED_ACTIVE_HOURS` full frame-hours inside it are busy
    and not on a rest day (see `_activity_clock`). A rest day is a weekday whose full
    dates (at least 2) are all empty. Under two weeks (some weekday has fewer than 2
    full dates) rest days are not judged: only busy hours on frame-dates that have rows
    count, so a silence counts only when it starts or ends inside busy hours. Overnight
    quiet and rest days are not gaps. Rows on fewer than 3 frame-dates are too little
    to tell a rest day from an outage, so no silence counts. Each gap runs from the last
    call before the silence to the first call after it.

    Rows have no quota, limit or error field, and no plan cap, so those signals are not
    available; the break in the export's own daily/weekly clock is the evidence used."""
    gaps, _judged, _warn = _judge_gaps(rows, max_gap_hours, tz)
    return gaps


def active_elapsed_days(t, t0, gaps):
    """Days from `t0` to `t` with detected zero-row gaps removed."""
    seconds = (t - t0).total_seconds()
    for g in gaps:
        if g["end"] <= t:
            seconds -= g["seconds"]
        elif g["start"] < t:
            seconds -= (t - g["start"]).total_seconds()
            break
    return seconds / 86400


def quota_cycles(rows, gaps, pricing, has_cost):
    """One measured cycle per gap: spend and tokens from the previous resume (or the export
    start, flagged partial) up to the call that precedes the silence."""
    if not rows or not gaps:
        return []
    cycles, resume, partial = [], rows[0]["time"], True
    for g in gaps:
        window = [r for r in rows if resume <= r["time"] <= g["start"]]
        if has_cost:
            usd = sum(r["cost"] for r in window)
        else:
            usd = sum(orgopt.call_cost_usd(r["input"], r["cached"], r["output"] + r.get("reasoning", 0.0), pricing)
                      for r in window)
        tokens = sum(r["input"] + r["output"] + r.get("reasoning", 0.0) for r in window)
        cycles.append({"start": resume.isoformat(), "end": g["start"].isoformat(),
                       "days": (g["start"] - resume).total_seconds() / 86400,
                       "usd": usd, "tokens": tokens, "partial": partial})
        resume, partial = g["end"], False
    return cycles


def _public_gaps(gaps):
    return [{"start": g["start"].isoformat(), "end": g["end"].isoformat(), "days": g["days"]} for g in gaps]


def span_and_active_days(t0, t1, gaps):
    """Calendar span and the rate denominator (span minus zero-row gaps), both in days."""
    span_seconds = (t1 - t0).total_seconds()
    span_days = max(span_seconds / 86400, 1 / 24)
    if not gaps:
        return span_days, span_days
    active = max((span_seconds - sum(g["seconds"] for g in gaps)) / 86400, 1 / 24)
    return span_days, active


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


def growth_per_day(days, ctx):
    """Context growth per day between hand-overs: a line per segment, weighted by segment length.
    Returns (growth, segments)."""
    segments = []
    for a, b in split_resets(days, ctx):
        slope, icpt = _fit(days[a:b], ctx[a:b])
        segments.append({"start": days[a], "end": days[b - 1] if b - 1 > a else days[a],
                         "n": b - a, "slope": slope, "c0": max(icpt + slope * days[a], 0.0) if b - a >= 3 else ctx[a]})
    fitted = [s for s in segments if s["n"] >= 3 and s["end"] > s["start"]]
    weight = sum(s["end"] - s["start"] for s in fitted)
    growth = sum(s["slope"] * (s["end"] - s["start"]) for s in fitted) / weight if weight else 0.0
    return growth, segments


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
            has_cost=False, lang="en", max_gap_hours=DEFAULT_MAX_GAP_HOURS, tz=None):
    thr = pricing.get("long_context_threshold") or math.inf
    if not rows:
        raise UsageError(t("u.err_empty", lang))
    assign_units(rows)
    t0, t1 = rows[0]["time"], rows[-1]["time"]
    raw_gaps, rest_days_judged, rest_days_warning = _judge_gaps(rows, max_gap_hours, tz)
    # Rates use time the export actually covers. A hole that breaks the export's own clock
    # is the quota being empty, and folding it into the denominator makes the same pace look cheaper.
    span_days, active_days = span_and_active_days(t0, t1, raw_gaps)
    by_unit = {}
    for r in rows:
        by_unit.setdefault(r["unit"], []).append(r)

    units, total_usd = [], 0.0
    for name, rs in by_unit.items():
        usd = sum(r["cost"] for r in rs) if has_cost else sum(
            orgopt.call_cost_usd(r["input"], r["cached"], r["output"] + r["reasoning"], pricing) for r in rs)
        total_usd += usd
        wakes = sessionize(rs, gap_seconds)
        for w in wakes:
            for j, r in enumerate(w):
                r["wake_start"] = j == 0
        ctx = [w[0]["input"] for w in wakes]
        days = [active_elapsed_days(w[0]["time"], t0, raw_gaps) for w in wakes]
        growth, segments = growth_per_day(days, ctx)
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

        growth_wake = statistics.mean(wake_growth(w) for w in wakes)
        parts = [cost_parts(r, pricing) for r in rs]
        unc_usd, cached_usd, out_usd, premium = (sum(p[i] for p in parts) for i in range(4))
        bounds = split_resets(days, ctx)
        peaks = [max(ctx[a:b]) for a, b in bounds[:-1]]
        floors = [ctx[a] for a, _ in bounds[1:]]
        sawtooth = len(peaks) >= 2 and statistics.mean(peaks) > 0 and \
            statistics.pstdev(peaks) / statistics.mean(peaks) < 0.25

        def days_to(target):
            if c_now >= target:
                return 0.0
            return (target - c_now) / growth if growth > 0 else math.inf

        units.append({
            "unit": name, "calls": len(rs), "wakes": len(wakes), "wakes_per_day": len(wakes) / active_days,
            "rounds_per_wake": len(rs) / len(wakes), "C_now": c_now, "C_max": max(ctx),
            "C_growth_per_day": growth, "resets_seen": len(segments) - 1, "segments": segments,
            "days_to_long_context": days_to(thr), "days_to_soft_cap": days_to(soft_cap) if soft_cap else None,
            "cache_hit": cached / inp if inp else 0.0, "input_tokens": inp, "output_tokens": out,
            "reasoning_tokens": reasoning, "output_per_wake": (out + reasoning) / len(wakes), "usd": usd,
            "short_wakes": len(short), "short_wake_usd": sum(cost1(w[0]) for w in short),
            "read_first_wakes": len(read_first), "read_first_usd": sum(cost1(w[0]) for w in read_first),
            **fetch_stats(read_first),
            "long_context_calls": len(long_calls), "long_context_extra_usd": long_extra,
            "uncached_input_usd": unc_usd, "cached_input_usd": cached_usd, "output_usd": out_usd,
            "long_premium_usd": premium,
            "miss_premium_usd": sum(miss_premium(r, pricing) for r in rs),
            "in_wake_miss_premium_usd": sum(miss_premium(r, pricing) for w in wakes for r in w[1:]),
            "C_p10": percentile(ctx, 0.1), "C_p50": percentile(ctx, 0.5), "C_p90": percentile(ctx, 0.9),
            "C_peak": statistics.median(peaks) if peaks else max(ctx),
            "C_floor": statistics.median(floors) if floors else min(ctx),
            "sawtooth": sawtooth, "growth_per_wake": growth_wake,
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
    anatomy = {k: sum(u[k] for u in units) for k in
               ("uncached_input_usd", "cached_input_usd", "output_usd", "long_premium_usd",
                "miss_premium_usd", "in_wake_miss_premium_usd", "short_wake_usd", "read_first_usd")}
    return {"calibration": calibrate(units, total_usd, pricing), "anatomy": anatomy, "has_cost": has_cost,
            "period_start": t0.isoformat(), "period_end": t1.isoformat(), "span_days": span_days,
            "active_days": active_days, "usage_gaps": _public_gaps(raw_gaps),
            "rest_days_judged": rest_days_judged, "rest_days_warning": rest_days_warning,
            "quota_cycles": quota_cycles(rows, raw_gaps, pricing, has_cost),
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
         ""]
    if res.get("usage_gaps"):
        items = "; ".join(f"{g['start'][:16]} – {g['end'][:16]} ({g['days']:.1f} d)" for g in res["usage_gaps"])
        L += [t("u.gap_note", lang, items=items, active=res["active_days"], span=res["span_days"]), ""]
    L += [t("u.total", lang, usd=res["total_usd"]), "", t("u.unit_note", lang), "",
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


def notes(mapping, skipped, lang="en", info=None):
    info = info or {}
    out = [t("u.mapping", lang, m=mapping) + (f"; {t('u.skipped', lang, n=skipped)}" if skipped else "")]
    if info.get("split_input"):
        out.append(t("u.split_input", lang))
    if info.get("cloud_agent_rows"):
        out.append(t("u.cloud_agent", lang, n=info["cloud_agent_rows"], col=CLOUD_AGENT_COL))
    if info.get("cost_text_rows"):
        out.append(t("u.cost_text", lang, n=info["cost_text_rows"], col=info.get("cost_column", "cost")))
    if info.get("bot_empty_rows"):
        out.append(t("u.bot_empty", lang, n=info["bot_empty_rows"], total=info.get("rows_loaded", 0),
                     col=mapping.get("bot", "bot"), by=mapping.get("model", "(all)")))
    if "bot" not in mapping:
        out.append(t("u.no_bot", lang) + t("u.use_chat" if "chat" in mapping else "u.all_one", lang))
    if "cached" not in mapping:
        out.append(t("u.no_cache", lang))
    return out


def run(csv_path, pricing, overrides=None, cached_separate=False, gap=120, soft_cap=None, lang="en",
        max_gap_hours=DEFAULT_MAX_GAP_HOURS):
    info = {}
    rows, mapping, skipped = load_rows(csv_path, overrides, cached_separate, lang, info)
    res = analyze(rows, pricing, gap, soft_cap=soft_cap, has_cost="cost" in mapping, lang=lang,
                  max_gap_hours=max_gap_hours)
    res["column_mapping"], res["skipped_rows"], res["load_info"] = mapping, skipped, info
    return res


if __name__ == "__main__":
    import sys
    try:
        from .cli import main
    except ImportError:
        from cli import main
    sys.exit(main(["analyze"] + sys.argv[1:]))
