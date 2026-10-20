"""schedules — when each bot is woken by a schedule, so wake-ups can be attributed without a bot id.

A schedules file has one bot per line (blank lines and # comments are ignored):

    # name        schedule                         options
    monitor       every 30m
    patrol        every 2h 08:00-22:00             in=grok-bot-default
    digest        at 09:00,18:00
    sync          cron */15 9-18 * * 1-5

    every N(m|h)  [HH:MM-HH:MM]   fires every N minutes (aligned to midnight), optionally only inside a daily window
    at HH:MM[,HH:MM...]          fires at fixed times every day
    cron M H DOM MON DOW         standard 5-field cron (*, */n, a-b, a-b/n, lists; DOW 0 or 7 = Sunday)
    in=BUCKET                    only match wake-ups of this context / model bucket

Times are in the report's time zone (--tz). Standard library only.
"""
from datetime import datetime, timedelta


class ScheduleError(ValueError):
    pass


def _dur(tok):
    tok = tok.strip().lower()
    try:
        if tok.endswith("m"):
            return int(tok[:-1])
        if tok.endswith("h"):
            return int(float(tok[:-1]) * 60)
    except ValueError:
        pass
    raise ScheduleError(f"cannot read interval {tok!r}; use e.g. 30m or 2h")


def _hhmm(tok):
    try:
        h, m = tok.strip().split(":")
        h, m = int(h), int(m)
    except ValueError:
        raise ScheduleError(f"cannot read time {tok!r}; use HH:MM")
    if not (0 <= h <= 24 and 0 <= m < 60):
        raise ScheduleError(f"time out of range: {tok!r}")
    return h * 60 + m


def _cron_field(tok, lo, hi):
    out = set()
    for part in tok.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = b = int(part)
            if step != 1:
                b = hi
        if a < lo or b > hi or a > b or step < 1:
            raise ScheduleError(f"cron field {tok!r} out of range {lo}-{hi}")
        out.update(range(a, b + 1, step))
    return out


def parse_line(line, lineno=0):
    toks = line.split()
    if len(toks) < 3:
        raise ScheduleError(f"line {lineno}: expected 'NAME every|at|cron ...'")
    name, kind, rest = toks[0], toks[1].lower(), toks[2:]
    opts = {}
    while rest and "=" in rest[-1]:
        k, v = rest.pop().split("=", 1)
        opts[k] = v
    spec = {"name": name, "kind": kind, "text": " ".join(toks[1:len(toks) - len(opts)]), "in": opts.get("in")}
    try:
        if kind == "every":
            spec["every"] = _dur(rest[0])
            if spec["every"] <= 0:
                raise ScheduleError("interval must be positive")
            if len(rest) > 1:
                a, b = rest[1].split("-", 1)
                spec["window"] = (_hhmm(a), _hhmm(b))
        elif kind == "at":
            spec["at"] = sorted(_hhmm(x) for x in " ".join(rest).replace(" ", "").split(",") if x)
            if not spec["at"]:
                raise ScheduleError("no times given")
        elif kind == "cron":
            if len(rest) != 5:
                raise ScheduleError("cron needs 5 fields: minute hour day-of-month month day-of-week")
            m, h, dom, mon, dow = rest
            spec["cron"] = (_cron_field(m, 0, 59), _cron_field(h, 0, 23), _cron_field(dom, 1, 31),
                            _cron_field(mon, 1, 12), {d % 7 for d in _cron_field(dow, 0, 7)},
                            dom != "*", dow != "*")
        else:
            raise ScheduleError(f"unknown schedule kind {kind!r}; use every, at or cron")
    except ScheduleError as e:
        raise ScheduleError(f"line {lineno}: {e}")
    except (ValueError, IndexError):
        raise ScheduleError(f"line {lineno}: cannot read {line.strip()!r}")
    return spec


def parse(text):
    specs, errors = [], []
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            specs.append(parse_line(line, i))
        except ScheduleError as e:
            errors.append(str(e))
    names = [s["name"] for s in specs]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        errors.append(f"duplicate bot names: {', '.join(dup)}")
    if errors:
        raise ScheduleError("; ".join(errors))
    return specs


def load(path):
    with open(path, encoding="utf-8") as f:
        return parse(f.read())


def _fires_on(spec, minute_of_day, day):
    k = spec["kind"]
    if k == "every":
        if minute_of_day % spec["every"]:
            return False
        w = spec.get("window")
        return w is None or w[0] <= minute_of_day <= w[1]
    if k == "at":
        return minute_of_day in spec["at"]
    mins, hours, doms, mons, dows, dom_set, dow_set = spec["cron"]
    if minute_of_day % 60 not in mins or minute_of_day // 60 not in hours or day.month not in mons:
        return False
    dow = (day.weekday() + 1) % 7            # cron: 0 = Sunday
    if dom_set and dow_set:                  # cron rule: either restriction matches
        return day.day in doms or dow in dows
    return (day.day in doms) and (dow in dows)


def fire_times(spec, start, end, tz):
    """Every scheduled fire between start and end (aware datetimes), as aware datetimes in tz."""
    t = start.astimezone(tz).replace(second=0, microsecond=0)
    e = end.astimezone(tz)
    out = []
    while t <= e:
        if _fires_on(spec, t.hour * 60 + t.minute, t):
            out.append(t)
        t += timedelta(minutes=1)
    return out


def interval_minutes(spec):
    """Typical minutes between fires (for wording and for proposing a new interval)."""
    if spec["kind"] == "every":
        return spec["every"]
    if spec["kind"] == "at":
        return 1440 / len(spec["at"])
    # cron: count fires over a fixed reference week
    ref = datetime(2026, 1, 5)          # a Monday
    n = sum(1 for i in range(7 * 1440) if _fires_on(spec, i % 1440, ref + timedelta(minutes=i)))
    return 7 * 1440 / n if n else float("inf")


NICE = [5, 10, 15, 20, 30, 60, 120, 180, 240, 360, 480, 720, 1440]


def nice_interval(minutes):
    """Smallest 'nice' interval at or above `minutes`."""
    for n in NICE:
        if n >= minutes - 1e-9:
            return n
    return NICE[-1]


def fmt_interval(minutes, lang="en"):
    if minutes % 60 == 0:
        h = minutes // 60
        return (f"{h} 小時" if lang == "zh-TW" else f"{h}h")
    return (f"{minutes:g} 分鐘" if lang == "zh-TW" else f"{minutes:g}m")
