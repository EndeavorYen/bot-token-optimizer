#!/usr/bin/env python3
"""redesign — search for a cheaper organization of the same bots, using the cost model.

Starting from an org (org.txt / org.json, ideally built from measured contexts plus who-talks-to-whom),
it tries changes one at a time and keeps the one that saves most, until nothing saves at least
MIN_GAIN of the total:

  instruction-level   drop acknowledgements, put board content in the message, one-line group status
  structural          retire a pure relay (redundant bot), merge two bots that hand work to each other
                      or share a manager, split an overloaded bot, add summarizing leaders where a
                      manager has more direct reports than the span limit allows

Bots marked `keep` are never retired or absorbed. Every number comes from orgopt.evaluate_org, so a
change survives only if the formula says it is cheaper; whether two jobs can really live in one bot is
a judgment the advisor makes before recommending a merge. Standard library only.
"""
import copy
import math

try:
    from . import orgopt
    from .i18n import t
except ImportError:          # run as a plain script
    import orgopt
    from i18n import t

MIN_GAIN = 0.02             # a change must save at least 2% of the current total to be kept
ACK_TOKENS = 30
GROUP_STATUS_TOKENS = 60
GROUP_HEAVY = 300
COORD_MSGS, COORD_TOKENS = 2, 300   # what two halves of a split bot send each other per day
LEADER_BASE = 8000
SPLIT_MIN_SOURCES = 3      # only a bot that serves several sources is worth splitting
EVAL_DAYS = 28
DEFAULT_POLICY = 120000     # compare structures as if every bot hands over at this context size
RELAY_ROUNDS = 0.5          # a relay does (almost) no tool work of its own


# ---------------------------------------------------------------- helpers
def steady(org):
    """Under a hand-over policy, start every context that will reach the threshold at the start of a
    cycle (a fresh chat), and the others where they are: no structure gets a head start."""
    pol = org.get("handover_at")
    if not pol:
        return org
    plain = {r["bot"]: r for r in orgopt.evaluate_org(dict(org, handover_at=None))["bots"]}
    o = copy.deepcopy(org)
    for b in o["bots"]:
        r = plain[b["name"]]
        base = float(b.get("base_tokens", min(b["context_tokens"], 8000)))
        if r["C_end"] >= pol:
            b["context_tokens"] = base + float(org.get("handover_tokens", orgopt.DEFAULT_HANDOVER))
    return o


def _total(org, pricing, cap):
    return orgopt.evaluate_org(steady(org), pricing, cap)["total_eq"]


def _bot(org, name):
    return next(b for b in org["bots"] if b["name"] == name)


def _links_in(org, name):
    return [ln for ln in org.get("links", []) if ln["to"] == name]


def _links_out(org, name):
    return [ln for ln in org.get("links", []) if ln["from"] == name]


def _add_link(org, a, b, n, tok):
    """Add messages a → b, merging with an existing link (message-weighted token average)."""
    if a == b or n <= 0:
        return
    for ln in org["links"]:
        if ln["from"] == a and ln["to"] == b:
            tot = ln["messages_per_day"] + n
            ln["avg_tokens"] = (ln["avg_tokens"] * ln["messages_per_day"] + tok * n) / tot
            ln["messages_per_day"] = tot
            return
    org["links"].append({"from": a, "to": b, "messages_per_day": n, "avg_tokens": tok})


def _prune_groups(org):
    org["groups"] = [g for g in org.get("groups", []) if len(g["members"]) >= 2]


def _fresh(org):
    o = copy.deepcopy(org)
    o.setdefault("links", [])
    o.setdefault("groups", [])
    return o


# ---------------------------------------------------------------- instruction-level changes
def drop_acks(org):
    acks = [ln for ln in org.get("links", []) if ln["avg_tokens"] <= ACK_TOKENS]
    if not acks:
        return None
    o = _fresh(org)
    o["links"] = [ln for ln in o["links"] if ln["avg_tokens"] > ACK_TOKENS]
    return o, {"kind": "acks", "links": [f"{ln['from']} → {ln['to']}" for ln in acks]}


def inline_board(org):
    readers = [b["name"] for b in org["bots"] if b.get("reads_board_via_tool") and _links_in(org, b["name"])]
    if not readers:
        return None
    o = _fresh(org)
    for name in readers:
        b = _bot(o, name)
        extra = float(b.get("board_read_tokens", 2000))
        b["reads_board_via_tool"] = False
        b.pop("board_read_tokens", None)
        for ln in _links_in(o, name):
            ln["avg_tokens"] += extra          # the content now travels in the message itself
    return o, {"kind": "board", "bots": readers}


def slim_groups(org):
    heavy = [g["name"] for g in org.get("groups", []) if g["avg_tokens"] > GROUP_HEAVY]
    if not heavy:
        return None
    o = _fresh(org)
    for g in o["groups"]:
        if g["name"] in heavy:
            g["avg_tokens"] = GROUP_STATUS_TOKENS
    return o, {"kind": "group", "groups": heavy}


INSTRUCTION_MOVES = (drop_acks, inline_board, slim_groups)


# ---------------------------------------------------------------- structural changes
def retire_relay(org, name):
    """A bot with one upstream, one downstream, no work of its own (no tool rounds, no own wake-ups) that
    passes on about as much as it receives only relays (same test as orgopt's relay finding)."""
    b = _bot(org, name)
    ins, outs = _links_in(org, name), _links_out(org, name)
    if b.get("keep") or b.get("_made") or len(ins) != 1 or len(outs) != 1 or b.get("own_wakes_per_day", 0) > 0:
        return None
    if b.get("work_rounds_per_wake", 1) > RELAY_ROUNDS or ins[0]["from"] == outs[0]["to"]:
        return None
    if outs[0]["messages_per_day"] < 0.7 * ins[0]["messages_per_day"] or outs[0]["avg_tokens"] < 0.6 * ins[0]["avg_tokens"]:
        return None
    up, down = ins[0]["from"], outs[0]["to"]
    o = _fresh(org)
    o["bots"] = [x for x in o["bots"] if x["name"] != name]
    o["links"] = [ln for ln in o["links"] if name not in (ln["from"], ln["to"])]
    _add_link(o, up, down, outs[0]["messages_per_day"], max(outs[0]["avg_tokens"], ins[0]["avg_tokens"]))
    for g in o["groups"]:
        if name in g["members"]:
            if g.get("wake", "addressed") == "addressed":
                # the share of posts addressed to the retired bot goes away with it
                g["messages_per_day"] *= (len(g["members"]) - 1) / len(g["members"])
            g["members"] = [m for m in g["members"] if m != name]
    _prune_groups(o)
    return o, {"kind": "retire", "bot": name, "up": up, "down": down}


def merge(org, survivor, absorbed, merge_cap):
    """Fold `absorbed` into `survivor`: one context holding both jobs, no messages between them."""
    a, s = _bot(org, absorbed), _bot(org, survivor)
    if a.get("keep") or a.get("_made") or s.get("_made"):
        return None
    base_a = float(a.get("base_tokens", min(a["context_tokens"], 8000)))
    base_s = float(s.get("base_tokens", min(s["context_tokens"], 8000)))
    c = s["context_tokens"] + a["context_tokens"]
    if c > merge_cap:
        return None
    if org.get("handover_at"):
        c = min(c, org["handover_at"])
    o = _fresh(org)
    m = _bot(o, survivor)
    ev = {r["bot"]: r for r in orgopt.evaluate_org(org)["bots"]}
    wa, ws = ev[absorbed]["wakes_per_day"] or 1e-9, ev[survivor]["wakes_per_day"] or 1e-9

    def avg(key, default):
        return (float(a.get(key, default)) * wa + float(s.get(key, default)) * ws) / (wa + ws)

    m["context_tokens"] = c
    m["base_tokens"] = base_a + base_s       # a fresh chat must carry both jobs' instructions and memory
    m["own_wakes_per_day"] = float(a.get("own_wakes_per_day", 0)) + float(s.get("own_wakes_per_day", 0))
    m["work_rounds_per_wake"] = avg("work_rounds_per_wake", 1)
    m["output_tokens_per_wake"] = avg("output_tokens_per_wake", 300)
    m["cache_hit_ratio"] = min(float(a.get("cache_hit_ratio", orgopt.DEFAULT_CACHE_HIT)),
                               float(s.get("cache_hit_ratio", orgopt.DEFAULT_CACHE_HIT)))
    if a.get("reasoning_tokens_per_wake") or s.get("reasoning_tokens_per_wake"):
        m["reasoning_tokens_per_wake"] = avg("reasoning_tokens_per_wake", 0)
    if a.get("reads_board_via_tool"):
        m["reads_board_via_tool"] = True
    m.pop("growth_per_day", None)
    o["bots"] = [x for x in o["bots"] if x["name"] != absorbed]
    # what `absorbed` passed to `survivor` beyond its own writing (e.g. board content it forwarded) still
    # has to reach the merged bot, so it rides on the messages that used to reach `absorbed`
    out_a = float(a.get("output_tokens_per_wake", 300))
    excess = sum(ln["messages_per_day"] * max(0.0, ln["avg_tokens"] - out_a)
                 for ln in o["links"] if ln["from"] == absorbed and ln["to"] == survivor)
    inbound = [ln for ln in o["links"] if ln["to"] == absorbed and ln["from"] != survivor]
    n_in = sum(ln["messages_per_day"] for ln in inbound)
    for ln in inbound:
        ln["avg_tokens"] += excess / n_in if n_in else 0.0
    links = o["links"]
    o["links"] = []
    for ln in links:
        f = survivor if ln["from"] == absorbed else ln["from"]
        to = survivor if ln["to"] == absorbed else ln["to"]
        _add_link(o, f, to, ln["messages_per_day"], ln["avg_tokens"])   # internal hand-offs disappear
    for g in o["groups"]:
        g["members"] = list(dict.fromkeys(survivor if x == absorbed else x for x in g["members"]))
    _prune_groups(o)
    # the work itself does not shrink: keep the tool rounds and the writing per day of both bots,
    # spread over the merged bot's (fewer) wake-ups
    w_new = {r["bot"]: r for r in orgopt.evaluate_org(o)["bots"]}[survivor]["wakes_per_day"] or 1e-9
    for key, default in (("work_rounds_per_wake", 1), ("output_tokens_per_wake", 300), ("reasoning_tokens_per_wake", 0)):
        total = float(a.get(key, default)) * wa + float(s.get(key, default)) * ws
        if total or key in m:
            m[key] = total / w_new
    return o, {"kind": "merge", "bot": absorbed, "into": survivor, "context": c}


def split(org, name):
    """Split an overloaded bot that serves several sources into two, each with half the sources."""
    b0 = _bot(org, name)
    if b0.get("keep") or b0.get("_made") or b0.get("_split"):
        return None
    ins = _links_in(org, name)
    sources = sorted({ln["from"] for ln in ins})
    if len(sources) < SPLIT_MIN_SOURCES:
        return None
    load = {src: sum(ln["messages_per_day"] * ln["avg_tokens"] for ln in ins if ln["from"] == src) for src in sources}
    half_a, half_b, wa, wb = [], [], 0.0, 0.0
    for src in sorted(sources, key=lambda x: -load[x]):
        if wa <= wb:
            half_a.append(src)
            wa += load[src]
        else:
            half_b.append(src)
            wb += load[src]
    o = _fresh(org)
    b = _bot(o, name)
    twin = dict(copy.deepcopy(b), name=f"{name}-2", _made=True)
    twin.pop("keep", None)
    b["_split"] = True
    base = float(b.get("base_tokens", min(b["context_tokens"], 8000)))
    for x in (b, twin):
        x["context_tokens"] = base + (b["context_tokens"] - base) / 2
        x["own_wakes_per_day"] = float(b.get("own_wakes_per_day", 0)) / 2
        x.pop("growth_per_day", None)
    o["bots"].append(twin)
    for ln in o["links"]:
        if ln["to"] == name and ln["from"] in half_b:
            ln["to"] = twin["name"]
    for ln in [x for x in o["links"] if x["from"] == name]:
        ln["messages_per_day"] /= 2
        o["links"].append(dict(ln, **{"from": twin["name"]}))
    _add_link(o, name, twin["name"], COORD_MSGS, COORD_TOKENS)
    _add_link(o, twin["name"], name, COORD_MSGS, COORD_TOKENS)
    for g in o["groups"]:
        if name in g["members"]:
            g["members"].append(twin["name"])
    return o, {"kind": "split", "bot": name, "twin": twin["name"], "a": half_a, "b": half_b}


def add_leaders(org, name, cap):
    """A manager with more direct reports than the span limit gets summarizing leaders."""
    if _bot(org, name).get("_made"):
        return None
    ins = _links_in(org, name)
    sources = sorted({ln["from"] for ln in ins})
    if len(sources) <= cap:
        return None
    m = sum(ln["avg_tokens"] for ln in ins) / len(ins)
    k = max(2, min(cap, round(orgopt.optimal_span_closed(LEADER_BASE, m))))
    chunks = [sources[i:i + k] for i in range(0, len(sources), k)]
    o = _fresh(org)
    leaders = []
    for i, chunk in enumerate(chunks, 1):
        lead = f"{name}-lead{i}"
        leaders.append(lead)
        o["bots"].append({"name": lead, "context_tokens": LEADER_BASE, "base_tokens": LEADER_BASE,
                          "work_rounds_per_wake": 0, "_made": True})
        rate = 0.0
        for ln in o["links"]:
            if ln["to"] == name and ln["from"] in chunk:
                ln["to"] = lead
                rate += ln["messages_per_day"]
        _add_link(o, lead, name, rate / len(chunk), m)       # one summary per round of reports
    return o, {"kind": "leaders", "bot": name, "leaders": leaders, "span": k}


def structural_candidates(org, cap, merge_cap):
    names = [b["name"] for b in org["bots"]]
    out = []
    for n in names:
        out.append(lambda n=n: retire_relay(org, n))
        out.append(lambda n=n: split(org, n))
        out.append(lambda n=n: add_leaders(org, n, cap))
    pairs = set()
    for ln in org.get("links", []):
        pairs.add(tuple(sorted((ln["from"], ln["to"]))))
    ups = {}
    for ln in org.get("links", []):
        ups.setdefault(ln["to"], set()).add(ln["from"])
    by_up = {}
    for n, u in ups.items():
        if len(u) == 1:
            by_up.setdefault(next(iter(u)), []).append(n)
    for sibs in by_up.values():
        for i in range(len(sibs)):
            for j in range(i + 1, len(sibs)):
                pairs.add(tuple(sorted((sibs[i], sibs[j]))))
    work = {b["name"]: (float(b.get("work_rounds_per_wake", 1)), float(b["context_tokens"])) for b in org["bots"]}
    for a, b in sorted(pairs):
        if a != b and a in names and b in names:
            # the bot that does more of its own work keeps its chat and takes over the other's duties
            s, x = (a, b) if work[a] >= work[b] else (b, a)
            out.append(lambda s=s, x=x: merge(org, s, x, merge_cap))
    return out


# ---------------------------------------------------------------- search
def _greedy(org, moves_fn, pricing, cap, max_moves):
    cur = org
    cur_total = _total(cur, pricing, cap)
    steps = []
    for _ in range(max_moves):
        best = None
        for make in moves_fn(cur):
            got = make()
            if not got:
                continue
            o, mv = got
            try:
                tot = _total(o, pricing, cap)
            except ValueError:
                continue
            if tot < cur_total * (1 - MIN_GAIN) and (best is None or tot < best[0]):
                best = (tot, o, mv)
        if not best:
            break
        tot, cur, mv = best
        mv["saving_eq"] = cur_total - tot
        steps.append(mv)
        cur_total = tot
    return cur, cur_total, steps


def optimize(org, pricing=None, span_cap=None, merge_cap=None, max_moves=6):
    """Returns the current cost and two options: instruction changes only, and the best structure."""
    pricing = pricing or orgopt.load_pricing()
    cap = int(org.get("span_cap") or span_cap or orgopt.DEFAULT_SPAN_CAP)
    merge_cap = merge_cap or org.get("quality_soft_cap") or 120000
    base = _fresh(org)
    # without hand-overs every context grows without limit and the model would favour splitting
    # everything; compare structures under the same hand-over policy instead
    policy = base.get("handover_at") or base.get("quality_soft_cap") or DEFAULT_POLICY
    base["handover_at"] = policy
    # long-run comparison: four weeks, every context starting at most at the threshold, so a structure
    # does not look cheaper just because it happens to start with a fresh chat
    base["horizon_days"] = max(float(base.get("horizon_days", 7)), EVAL_DAYS)
    for x in base["bots"]:
        x["context_tokens"] = min(float(x["context_tokens"]), policy)
    base_total = _total(base, pricing, cap)
    a, a_total, a_steps = _greedy(base, lambda o: [lambda f=f, o=o: f(o) for f in INSTRUCTION_MOVES],
                                  pricing, cap, len(INSTRUCTION_MOVES))
    b, b_total, b_steps = _greedy(a, lambda o: structural_candidates(o, cap, merge_cap), pricing, cap, max_moves)
    H = base["horizon_days"]
    week = lambda eq: orgopt.to_usd(eq, pricing) * 7 / H
    for s in a_steps + b_steps:
        s["usd_week"] = week(s["saving_eq"])
    names_before = {x["name"] for x in org["bots"]}
    names_after = {x["name"] for x in b["bots"]}
    idle = [r["bot"] for r in orgopt.evaluate_org(org, pricing, cap)["bots"] if r["wakes_per_day"] == 0]
    for o in (base, a, b):
        for x in o["bots"]:
            x.pop("_made", None)
            x.pop("_split", None)
    return {"span_cap": cap, "merge_cap": merge_cap, "horizon_days": H, "policy": policy,
            "current": {"org": base, "usd_week": week(base_total)},
            "instructions": {"org": a, "usd_week": week(a_total), "steps": a_steps},
            "structure": {"org": b, "usd_week": week(b_total), "steps": b_steps},
            "removed": sorted(names_before - names_after), "added": sorted(names_after - names_before),
            "idle": idle}


# ---------------------------------------------------------------- output
def mermaid(org, pricing=None, removed=(), added=()):
    """A flowchart of who hands work to whom; labels show context and cost per week."""
    ev = {r["bot"]: r for r in orgopt.evaluate_org(org, pricing or orgopt.load_pricing())["bots"]}
    H = float(org.get("horizon_days", 7))
    ids = {b["name"]: f"n{i}" for i, b in enumerate(org["bots"])}
    L = ["```mermaid", "flowchart TD"]
    for b in org["bots"]:
        r = ev[b["name"]]
        label = f"{b['name']}<br/>{b['context_tokens'] / 1000:.0f}K · ${r['cost_usd'] * 7 / H:,.0f}/wk"
        L.append(f'    {ids[b["name"]]}["{label}"]')
    for ln in org.get("links", []):
        L.append(f'    {ids[ln["from"]]} -->|"{ln["messages_per_day"]:g}/d · {ln["avg_tokens"]:,.0f}"| {ids[ln["to"]]}')
    for i, g in enumerate(org.get("groups", [])):
        gid = f"g{i}"
        L.append(f'    {gid}(["group {g["name"]}<br/>{g["messages_per_day"]:g}/d · {g["avg_tokens"]:,.0f}"])')
        for mname in g["members"]:
            L.append(f"    {gid} -.- {ids[mname]}")
    new = [ids[n] for n in added if n in ids]
    if new:
        L.append("    classDef new stroke-width:3px,stroke-dasharray:4 2")
        L.append(f"    class {','.join(new)} new")
    L.append("```")
    return "\n".join(L)


def _step_text(s, lang):
    k = s["kind"]
    if k == "acks":
        return t("rd.acks", lang, links=", ".join(s["links"]))
    if k == "board":
        return t("rd.board", lang, bots=", ".join(s["bots"]))
    if k == "group":
        return t("rd.group", lang, groups=", ".join(s["groups"]), tok=GROUP_STATUS_TOKENS)
    if k == "retire":
        return t("rd.retire", lang, bot=s["bot"], up=s["up"], down=s["down"])
    if k == "merge":
        return t("rd.merge", lang, bot=s["bot"], into=s["into"], c=f"{s['context']:,.0f}")
    if k == "split":
        return t("rd.split", lang, bot=s["bot"], twin=s["twin"], a=", ".join(s["a"]), b=", ".join(s["b"]))
    if k == "leaders":
        return t("rd.leaders", lang, bot=s["bot"], n=len(s["leaders"]), k=s["span"])
    return k


def _step_request(s, lang):
    k = s["kind"]
    if k == "acks":
        return t("p.ack.req", lang)
    if k == "board":
        return t("p.read_first.req", lang)
    return t(f"rd.req.{k}", lang, **{kk: (", ".join(v) if isinstance(v, list) else v) for kk, v in s.items()
                                     if kk not in ("kind", "saving_eq", "usd_week")})


def redesign_md(res, pricing=None, lang="en"):
    cur, a, b = res["current"], res["instructions"], res["structure"]
    L = [f"## {t('rd.title', lang)}", "",
         t("rd.summary", lang, now=cur["usd_week"], a=a["usd_week"], ra=a["usd_week"] / cur["usd_week"] if cur["usd_week"] else 1,
           b=b["usd_week"], rb=b["usd_week"] / cur["usd_week"] if cur["usd_week"] else 1), ""]
    steps = res["instructions"]["steps"] + res["structure"]["steps"]
    retired = [x["bot"] for x in steps if x["kind"] == "retire"]
    merged = [f"{x['bot']} → {x['into']}" for x in steps if x["kind"] == "merge"]
    if retired:
        L += [t("rd.redundant", lang, bots=", ".join(retired)), ""]
    if merged:
        L += [t("rd.merged", lang, pairs=", ".join(merged)), ""]
    if res["idle"]:
        L += [t("rd.idle", lang, bots=", ".join(res["idle"])), ""]
    L += [t("rd.header", lang), "|---|---|---|---|"]
    for i, s in enumerate(a["steps"] + b["steps"], 1):
        level = t("rd.level.instr" if s in a["steps"] else "rd.level.struct", lang)
        L.append(f"| {i} | {_step_text(s, lang)} | {level} | ${s['usd_week']:,.2f} |")
    if not a["steps"] and not b["steps"]:
        L.append(t("rd.none", lang))
    L += ["", t("rd.note", lang, gain=MIN_GAIN, cap=res["span_cap"], merge=f"{res['merge_cap']:,.0f}",
                        policy=f"{res['policy']:,.0f}"), "",
          f"### {t('rd.chart_now', lang)}", "", mermaid(cur["org"], pricing), "",
          f"### {t('rd.chart_after', lang)}", "", mermaid(b["org"], pricing, res["removed"], res["added"])]
    reqs = [_step_request(s, lang) for s in a["steps"] + b["steps"]]
    if reqs:
        L += ["", f"### {t('m.paste', lang)}", "", "```text", t("m.paste_intro", lang)]
        L += [f"{i}. {r}" for i, r in enumerate(reqs, 1)]
        L += [t("m.paste_outro", lang), "```"]
    return "\n".join(L) + "\n"
