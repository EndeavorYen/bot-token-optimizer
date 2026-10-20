#!/usr/bin/env python3
"""bto — bot token optimizer command line.

    bto report usage.csv --platform grok-bot --goal-usd-week 300 --out memo.md   # one-page memo
    bto report usage.csv --schedules schedules.txt --tz +08:00                  # split buckets into bots
    bto analyze usage.csv --platform grok-bot --org-out org.json
    bto trend usage.csv --tz +08:00
    bto init                      # describe your bots by answering questions
    bto org org.txt               # diagnose (org.txt or org.json)
    bto compare before.txt after.txt
    bto redesign org.txt --keep chief    # who is redundant, what to merge or split, the new org chart
    bto board | scenario | span | reset | cost | convert | presets

Without installing: python3 core/cli.py <command> ...
Language: --lang en|zh-TW (default: BTO_LANG, then system locale).
"""
import argparse
import json
import math
import sys

try:
    from . import __version__, advise, analyze_usage, orgopt, orgspec, redesign, schedules
    from .i18n import resolve_lang
except ImportError:          # run as a plain script
    import advise
    import analyze_usage
    import orgopt
    import orgspec
    import redesign
    import schedules
    from i18n import resolve_lang
    __version__ = "0.6.0"


def _dump(obj):
    def clean(o):
        if isinstance(o, float) and (math.isinf(o) or math.isnan(o)):
            return None
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [clean(v) for v in o]
        return o
    print(json.dumps(clean(obj), ensure_ascii=False, indent=2, default=str))


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lang", default=argparse.SUPPRESS, help="en | zh-TW")
    common.add_argument("--platform", default=argparse.SUPPRESS,
                        help="platform preset name (e.g. grok-bot, generic) or path")
    common.add_argument("--pricing", default=argparse.SUPPRESS,
                        help="pricing preset name (e.g. grok-4.7, claude-sonnet-5-5) or path")

    ap = argparse.ArgumentParser(prog="bto", description="Bot token optimizer: find where your bots' tokens go.",
                                 parents=[common])
    ap.add_argument("--version", action="version", version=f"bto {__version__}")
    ap.set_defaults(lang=None, platform=None, pricing=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("analyze", parents=[common], help="analyze a usage CSV")
    s.add_argument("csv")
    s.add_argument("--map", action="append", default=[], help="field=CSV column, e.g. --map bot=Agent")
    s.add_argument("--cached-separate", action="store_true", help="cached tokens are NOT included in the input column")
    s.add_argument("--gap", type=float, default=120, help="seconds between calls that still count as one wake-up")
    s.add_argument("--soft-cap", type=float, help="context quality soft cap (tokens)")
    s.add_argument("--org-out", help="write an org.json skeleton here")
    s.add_argument("--json", action="store_true")

    def csv_args(s):
        s.add_argument("csv")
        s.add_argument("--map", action="append", default=[], help="field=CSV column, e.g. --map bot=Agent")
        s.add_argument("--cached-separate", action="store_true", help="cached tokens are NOT included in the input column")
        s.add_argument("--gap", type=float, default=120, help="seconds between calls that still count as one wake-up")
        s.add_argument("--tz", default="UTC", help="time zone for days, e.g. +08:00 (default UTC)")
        s.add_argument("--json", action="store_true")

    s = sub.add_parser("report", parents=[common], help="one-page consultant memo from a usage CSV")
    csv_args(s)
    s.add_argument("--goal-usd-week", type=float, help="weekly budget to test the plan against")
    s.add_argument("--handover-at", type=float, help="fix one hand-over threshold for every context (default: "
                                                     "pick per context from 40K-150K)")
    s.add_argument("--max-handovers-week", type=float, default=7, help="at most this many hand-overs a week per context")
    s.add_argument("--quota-days", type=float, help="the quota currently runs out after this many days")
    s.add_argument("--target-days", type=float, default=7, help="how many days the quota should last (default 7)")
    s.add_argument("--schedules", help="schedules file (one bot per line: NAME every 30m | at 09:00,18:00 | cron ...) "
                                       "to split buckets into bots")
    s.add_argument("--out", help="also write the memo to this file")
    s.add_argument("--org-out", help="write an org.json skeleton of the contexts and named bots (for bto redesign)")

    s = sub.add_parser("trend", parents=[common], help="spend per day and what changed")
    csv_args(s)

    s = sub.add_parser("quota", parents=[common], help="how long the quota lasts: budget and days after savings")
    s.add_argument("--week-usd", type=float, required=True, help="current spend per week")
    s.add_argument("--days-now", type=float, required=True, help="the quota currently lasts this many days")
    s.add_argument("--target-days", type=float, default=7)
    s.add_argument("--save", type=float, nargs="+", default=[], metavar="USD",
                   help="weekly savings to test: one value, or LOW HIGH")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("init", parents=[common], help="describe your bots interactively")
    s.add_argument("-o", "--out", default="org", help="output base name (writes <out>.txt and <out>.json)")

    s = sub.add_parser("convert", parents=[common], help="convert org.txt <-> org.json")
    s.add_argument("input")
    s.add_argument("-o", "--output", help="output path (.txt or .json); default: the other format next to the input")

    s = sub.add_parser("org", parents=[common], help="diagnose an organization (org.txt or org.json)")
    s.add_argument("file")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("redesign", parents=[common], help="search for a cheaper organization of the same bots")
    s.add_argument("file")
    s.add_argument("--merge-cap", type=float, help="largest merged context (default: soft_cap, else 120000)")
    s.add_argument("--max-moves", type=int, default=6, help="at most this many structural changes")
    s.add_argument("--keep", default="", help="comma-separated bots that must not be retired or merged away")
    s.add_argument("--out", help="write the redesigned org.txt here")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("compare", parents=[common], help="compare two organizations")
    s.add_argument("before")
    s.add_argument("after")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("board", parents=[common], help='is "write to a board, then ping" worth it?')
    s.add_argument("--k", type=int, default=1, help="tool-call rounds of own work per wake-up")
    s.add_argument("--mode", choices=["sequential", "parallel"], default="sequential")
    s.add_argument("--board", type=float, help="tokens the receiver reads from the board each time (enables the cost table)")
    s.add_argument("--section", type=float, default=2000, help="tokens of the section it actually needs")
    s.add_argument("--context", type=float, default=60000, help="receiver's context (tokens)")
    s.add_argument("--pings", type=float, default=10, help="notifications per day that send it to the board")
    s.add_argument("--handover-at", type=float, help="the receiver hands over at this context size")

    s = sub.add_parser("scenario", parents=[common], help="relay leader vs. summarizing leader")
    s.add_argument("kind", choices=["relay", "aggregate"])
    s.add_argument("--n", type=int, default=6)
    s.add_argument("--c-worker", type=float, default=30000)
    s.add_argument("--c-leader", type=float, default=60000)
    s.add_argument("--c-chief", type=float, default=100000)

    s = sub.add_parser("span", parents=[common], help="optimal number of bots per manager")
    s.add_argument("--n", type=int, required=True, help="number of workers")
    s.add_argument("--c0", type=float, required=True, help="manager base context")
    s.add_argument("--m", type=float, required=True, help="tokens per report")
    s.add_argument("--c0-chief", type=float)
    s.add_argument("--k-max", type=int, help="span limit (default: platform preset, else 8)")
    s.add_argument("--no-assign-wake", action="store_true")

    s = sub.add_parser("reset", parents=[common], help="is it time to hand over to a fresh chat?")
    s.add_argument("--c", type=float, required=True, help="current context")
    s.add_argument("--c-base", type=float, default=8000)
    s.add_argument("--handover", type=float, default=orgopt.DEFAULT_HANDOVER)
    s.add_argument("--wakes-per-day", type=float, default=10)
    s.add_argument("--horizon-days", type=float, default=7)
    s.add_argument("--rounds-per-wake", type=float, default=1)
    s.add_argument("--cache-hit", type=float, default=orgopt.DEFAULT_CACHE_HIT)
    s.add_argument("--soft-cap", type=float)

    s = sub.add_parser("cost", parents=[common], help="dollar cost of one call")
    s.add_argument("--input", type=float, required=True)
    s.add_argument("--cached", type=float, default=0)
    s.add_argument("--output", type=float, default=0)

    sub.add_parser("presets", parents=[common], help="list built-in pricing and platform presets")
    return ap


GLOBALS = ("--lang", "--platform", "--pricing")


def _extract_globals(argv):
    """Pull the global options out of argv so they work before or after the sub-command."""
    found, rest, i = {}, [], 0
    while i < len(argv):
        arg = argv[i]
        key = arg.split("=", 1)[0]
        if key in GLOBALS:
            if "=" in arg:
                found[key[2:]] = arg.split("=", 1)[1]
            elif i + 1 < len(argv):
                found[key[2:]] = argv[i + 1]
                i += 1
            i += 1
            continue
        rest.append(arg)
        i += 1
    return found, rest


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    glob, rest = _extract_globals(argv)
    a = build_parser().parse_args(rest)
    for k, v in glob.items():
        setattr(a, k, v)
    lang = resolve_lang(a.lang)
    try:
        plat = orgopt.load_platform(a.platform) if a.platform else {}
        pricing = orgopt.load_pricing(a.pricing or plat.get("pricing_path"))
        cap = plat.get("span_cap", orgopt.DEFAULT_SPAN_CAP)
        return _run(a, lang, pricing, cap)
    except BrokenPipeError:          # e.g. piped into `head`
        import os
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except (orgspec.OrgSpecError, analyze_usage.UsageError, schedules.ScheduleError, FileNotFoundError, ValueError) as e:
        print(f"bto: {e}", file=sys.stderr)
        return 2


def _load(path, lang):
    org, warns = orgspec.load_org(path, lang)
    for w in warns:
        print(f"bto: {w}", file=sys.stderr)
    return org


def _run(a, lang, pricing, cap):
    if a.cmd == "analyze":
        overrides = dict(m.split("=", 1) for m in a.map)
        res = analyze_usage.run(a.csv, pricing, overrides, a.cached_separate, a.gap, a.soft_cap, lang)
        if a.org_out:
            with open(a.org_out, "w", encoding="utf-8") as f:
                json.dump(analyze_usage.org_skeleton(res, lang=lang), f, ensure_ascii=False, indent=2)
        if a.json:
            _dump(res)
        else:
            print("\n".join(analyze_usage.notes(res["column_mapping"], res["skipped_rows"], lang, res.get("load_info"))) + "\n")
            print(analyze_usage.report_md(res, lang))
    elif a.cmd in ("report", "trend"):
        overrides = dict(m.split("=", 1) for m in a.map)
        tz = advise.parse_tz(a.tz)
        specs = schedules.load(a.schedules) if getattr(a, "schedules", None) else None
        rep = advise.report(a.csv, pricing, overrides, a.cached_separate, a.gap,
                            getattr(a, "goal_usd_week", None), tz, getattr(a, "handover_at", None), specs, lang,
                            getattr(a, "max_handovers_week", 7), getattr(a, "quota_days", None),
                            getattr(a, "target_days", 7))
        if a.cmd == "trend":
            _dump(rep["trend"]) if a.json else print(advise.trend_md(rep["trend"], lang))
        else:
            text = advise.memo_md(rep, lang)
            if getattr(a, "org_out", None):
                with open(a.org_out, "w", encoding="utf-8") as f:
                    json.dump(advise.org_skeleton(rep), f, ensure_ascii=False, indent=2)
            if a.out:
                with open(a.out, "w", encoding="utf-8") as f:
                    f.write(text)
            if a.json:
                if rep.get("attribution"):
                    for b in rep["attribution"]["bots"]:
                        b.pop("spec", None)
                for e in rep["plan"]["entities"]:
                    e.pop("bot", None)
                _dump({k: v for k, v in rep.items() if k != "tz"})
            else:
                print(text)
    elif a.cmd == "quota":
        lo = a.save[0] if a.save else None
        hi = a.save[-1] if a.save else None
        q = advise.quota_math(a.week_usd, a.days_now, a.target_days, lo, hi)
        _dump(q) if a.json else print(advise.quota_md(q, lang))
    elif a.cmd == "init":
        orgspec.run_init(a.out, lang)
    elif a.cmd == "convert":
        org = _load(a.input, lang)
        out = a.output or (a.input.rsplit(".", 1)[0] + (".json" if a.input.endswith(".txt") else ".txt"))
        with open(out, "w", encoding="utf-8") as f:
            if out.endswith(".txt"):
                f.write(orgspec.to_txt(org))
            else:
                json.dump(org, f, ensure_ascii=False, indent=2)
        print(out)
    elif a.cmd == "org":
        res = orgopt.evaluate_org(_load(a.file, lang), pricing, cap, lang)
        _dump(res) if a.json else print(orgopt.org_report_md(res, lang))
    elif a.cmd == "redesign":
        org = _load(a.file, lang)
        for name in filter(None, (x.strip() for x in a.keep.split(","))):
            next((b for b in org["bots"] if b["name"] == name), {})["keep"] = True
        res = redesign.optimize(org, pricing, cap, a.merge_cap, a.max_moves)
        if a.out:
            with open(a.out, "w", encoding="utf-8") as f:
                f.write(orgspec.to_txt(res["structure"]["org"]))
        _dump(res) if a.json else print(redesign.redesign_md(res, pricing, lang))
    elif a.cmd == "compare":
        res = orgopt.compare_orgs(_load(a.before, lang), _load(a.after, lang), pricing, cap, lang)
        _dump(res) if a.json else print(orgopt.compare_report_md(res, lang))
    elif a.cmd == "board":
        r_cache, _, _ = orgopt.price_ratios(pricing)
        out = orgopt.board_overhead(a.k, a.mode, r_cache)
        if a.board:
            out["weekly_cost"] = orgopt.board_options(a.context, a.pings, a.board, a.section, k=a.k,
                                                      handover_at=a.handover_at, pricing=pricing)
        _dump(out)
    elif a.cmd == "scenario":
        fn = orgopt.scenario_relay if a.kind == "relay" else None
        _dump(fn(a.c_worker, a.c_leader, a.c_chief) if fn else
              orgopt.scenario_aggregate(a.n, a.c_worker, a.c_leader, a.c_chief))
    elif a.cmd == "span":
        _dump(orgopt.optimal_span(a.n, a.c0, a.m, a.c0_chief, a.k_max or cap, not a.no_assign_wake))
    elif a.cmd == "reset":
        _dump(orgopt.reset_decision(a.c, a.c_base, a.handover, a.wakes_per_day, a.horizon_days,
                                    a.rounds_per_wake, a.cache_hit, pricing, a.soft_cap, lang))
    elif a.cmd == "cost":
        print(f"{orgopt.call_cost_usd(a.input, a.cached, a.output, pricing):.6f}")
    elif a.cmd == "presets":
        _dump({"pricing": orgopt.list_presets("pricing"), "platforms": orgopt.list_presets("platforms")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
