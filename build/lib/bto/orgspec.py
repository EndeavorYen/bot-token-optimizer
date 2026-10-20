"""Organization description: validation, the compact org.txt format, and the `init` wizard.

org.txt — one line per bot, link or group (blank lines and # comments are ignored):

    settings horizon=7 soft_cap=120k
    bot chief      100k base=8k wakes=3 rounds=1
    bot researcher 30k  rounds=3 board
    chief -> researcher : 6/day 800
    writer -> researcher : 6/day 15
    group all-hands : chief researcher writer : 10/day 900 wake=all
"""
import json
import os
import re

try:
    from .i18n import t
except ImportError:          # run as a plain script
    from i18n import t


class OrgSpecError(ValueError):
    pass


TOP_KEYS = {"horizon_days", "quality_soft_cap", "handover_tokens", "span_cap", "bots", "links", "groups",
            "_note", "$schema"}
BOT_KEYS = {"name", "context_tokens", "base_tokens", "own_wakes_per_day", "work_rounds_per_wake",
            "reads_board_via_tool", "board_read_tokens", "output_tokens_per_wake", "reasoning_tokens_per_wake",
            "cache_hit_ratio", "growth_per_day"}
BOT_NUM = BOT_KEYS - {"name", "reads_board_via_tool", "cache_hit_ratio"}
LINK_KEYS = {"from", "to", "messages_per_day", "avg_tokens"}
GROUP_KEYS = {"name", "members", "messages_per_day", "avg_tokens", "wake"}


# ---------------------------------------------------------------- validation
def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0


def validate_org(org, lang="en"):
    """Return (errors, warnings) as lists of human-readable strings."""
    errs, warns = [], []
    if not isinstance(org, dict):
        return [t("v.no_bots", lang)], warns
    for k in org:
        if k not in TOP_KEYS:
            warns.append(t("v.unknown_key", lang, where="org", field=k))
    for k in ("horizon_days", "quality_soft_cap", "handover_tokens", "span_cap"):
        if k in org and org[k] is not None and not _is_num(org[k]):
            errs.append(t("v.num", lang, where="org", field=k, val=org[k]))
    bots = org.get("bots")
    if not isinstance(bots, list) or not bots:
        return errs + [t("v.no_bots", lang)], warns
    names = set()
    for i, b in enumerate(bots):
        where = f"bots[{i}]"
        if not isinstance(b, dict):
            errs.append(t("v.bot_obj", lang, i=i))
            continue
        if "name" in b:
            where = f"bot '{b['name']}'"
        for req in ("name", "context_tokens"):
            if req not in b:
                errs.append(t("v.missing", lang, where=where, field=req))
        if "name" in b:
            if b["name"] in names:
                errs.append(t("v.dup", lang, name=b["name"]))
            names.add(b["name"])
        for k, v in b.items():
            if k not in BOT_KEYS:
                warns.append(t("v.unknown_key", lang, where=where, field=k))
            elif k in BOT_NUM and not _is_num(v):
                errs.append(t("v.num", lang, where=where, field=k, val=v))
        if "cache_hit_ratio" in b and not (_is_num(b["cache_hit_ratio"]) and b["cache_hit_ratio"] <= 1):
            errs.append(t("v.ratio", lang, where=where, field="cache_hit_ratio", val=b["cache_hit_ratio"]))
    for i, ln in enumerate(org.get("links", []) or []):
        where = f"links[{i}]"
        if not isinstance(ln, dict):
            errs.append(t("v.missing", lang, where=where, field="from"))
            continue
        for req in ("from", "to", "messages_per_day", "avg_tokens"):
            if req not in ln:
                errs.append(t("v.missing", lang, where=where, field=req))
        for k in ln:
            if k not in LINK_KEYS:
                warns.append(t("v.unknown_key", lang, where=where, field=k))
        for k in ("from", "to"):
            if k in ln and ln[k] not in names:
                errs.append(t("v.unknown_bot", lang, where=where, name=ln[k]))
        for k in ("messages_per_day", "avg_tokens"):
            if k in ln and not _is_num(ln[k]):
                errs.append(t("v.num", lang, where=where, field=k, val=ln[k]))
    for i, g in enumerate(org.get("groups", []) or []):
        where = f"groups[{i}]"
        if not isinstance(g, dict):
            errs.append(t("v.members", lang, where=where))
            continue
        for req in ("name", "members", "messages_per_day", "avg_tokens"):
            if req not in g:
                errs.append(t("v.missing", lang, where=where, field=req))
        for k in g:
            if k not in GROUP_KEYS:
                warns.append(t("v.unknown_key", lang, where=where, field=k))
        mem = g.get("members")
        if "members" in g and (not isinstance(mem, list) or not mem):
            errs.append(t("v.members", lang, where=where))
        for m in mem if isinstance(mem, list) else []:
            if m not in names:
                errs.append(t("v.unknown_bot", lang, where=where, name=m))
        for k in ("messages_per_day", "avg_tokens"):
            if k in g and not _is_num(g[k]):
                errs.append(t("v.num", lang, where=where, field=k, val=g[k]))
        if g.get("wake", "addressed") not in ("addressed", "all"):
            errs.append(t("v.wake", lang, where=where))
    return errs, warns


def check_org(org, lang="en"):
    """Raise OrgSpecError listing every problem; return the warnings."""
    errs, warns = validate_org(org, lang)
    if errs:
        raise OrgSpecError("\n".join([t("v.title", lang)] + [f"  - {e}" for e in errs]))
    return warns


# ---------------------------------------------------------------- org.txt
_NUM = re.compile(r"^(\d+(?:\.\d+)?)([kKmM]?)$")
_LINK = re.compile(r"^(\S+)\s*->\s*(\S+)\s*:\s*(\S+?)\s*/\s*(?:day|d|天)\s+(\S+?)(?:\s*tokens?)?$")
_GROUP = re.compile(r"^group\s+(\S+)\s*:\s*(.+?)\s*:\s*(\S+?)\s*/\s*(?:day|d|天)\s+(\S+?)(?:\s+(wake=\S+))?$")
_BOT_OPTS = {"base": "base_tokens", "wakes": "own_wakes_per_day", "rounds": "work_rounds_per_wake",
             "out": "output_tokens_per_wake", "reasoning": "reasoning_tokens_per_wake",
             "cache": "cache_hit_ratio", "growth": "growth_per_day", "board": "board_read_tokens"}
_SETTINGS = {"horizon": "horizon_days", "soft_cap": "quality_soft_cap", "handover": "handover_tokens",
             "span_cap": "span_cap"}


def parse_number(tok, n=0, lang="en"):
    m = _NUM.match(tok.replace(",", "").replace("_", ""))
    if not m:
        raise OrgSpecError(t("t.number", lang, n=n, tok=tok))
    v = float(m.group(1)) * {"": 1, "k": 1e3, "K": 1e3, "m": 1e6, "M": 1e6}[m.group(2)]
    return int(v) if v == int(v) else v


def parse_txt(text, lang="en"):
    org = {"bots": [], "links": [], "groups": []}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("settings"):
            for opt in line.split()[1:]:
                k, _, v = opt.partition("=")
                if k not in _SETTINGS or not v:
                    raise OrgSpecError(t("t.option", lang, n=n, opt=opt))
                org[_SETTINGS[k]] = parse_number(v, n, lang)
        elif line.startswith("group "):
            m = _GROUP.match(line)
            if not m:
                raise OrgSpecError(t("t.syntax", lang, n=n, line=raw.strip()))
            g = {"name": m.group(1), "members": m.group(2).replace(",", " ").split(),
                 "messages_per_day": parse_number(m.group(3), n, lang), "avg_tokens": parse_number(m.group(4), n, lang)}
            if m.group(5):
                g["wake"] = m.group(5).split("=", 1)[1]
            org["groups"].append(g)
        elif line.startswith("bot "):
            parts = line.split()
            if len(parts) < 3:
                raise OrgSpecError(t("t.syntax", lang, n=n, line=raw.strip()))
            b = {"name": parts[1], "context_tokens": parse_number(parts[2], n, lang)}
            for opt in parts[3:]:
                k, eq, v = opt.partition("=")
                if k == "board":
                    b["reads_board_via_tool"] = True
                    if eq:
                        b["board_read_tokens"] = parse_number(v, n, lang)
                elif k in _BOT_OPTS and v:
                    b[_BOT_OPTS[k]] = parse_number(v, n, lang)
                else:
                    raise OrgSpecError(t("t.option", lang, n=n, opt=opt))
            org["bots"].append(b)
        else:
            m = _LINK.match(line)
            if not m:
                raise OrgSpecError(t("t.syntax", lang, n=n, line=raw.strip()))
            org["links"].append({"from": m.group(1), "to": m.group(2),
                                 "messages_per_day": parse_number(m.group(3), n, lang),
                                 "avg_tokens": parse_number(m.group(4), n, lang)})
    return org


def _k(v):
    v = float(v)
    if v >= 1000 and v % 1000 == 0:
        return f"{int(v // 1000)}k"
    return f"{v:g}"


def to_txt(org):
    out = []
    st = [f"{short}={_k(org[key])}" for short, key in _SETTINGS.items() if org.get(key) is not None]
    if st:
        out.append("settings " + " ".join(st))
    width = max((len(b["name"]) for b in org["bots"]), default=0)
    for b in org["bots"]:
        opts = []
        for short, key in _BOT_OPTS.items():
            if key in b and key != "board_read_tokens":
                opts.append(f"{short}={_k(b[key]) if key != 'cache_hit_ratio' else b[key]}")
        if b.get("reads_board_via_tool"):
            opts.append(f"board={_k(b['board_read_tokens'])}" if "board_read_tokens" in b else "board")
        out.append(f"bot {b['name']:<{width}} {_k(b['context_tokens'])} " + " ".join(opts))
    for ln in org.get("links", []):
        out.append(f"{ln['from']} -> {ln['to']} : {ln['messages_per_day']:g}/day {_k(ln['avg_tokens'])}")
    for g in org.get("groups", []):
        w = f" wake={g['wake']}" if g.get("wake") else ""
        out.append(f"group {g['name']} : {' '.join(g['members'])} : {g['messages_per_day']:g}/day {_k(g['avg_tokens'])}{w}")
    return "\n".join(line.rstrip() for line in out) + "\n"


def load_org(path, lang="en"):
    """Load org.json or org.txt, validate it, and return (org, warnings)."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    org = parse_txt(text, lang) if path.endswith(".txt") else json.loads(text)
    return org, check_org(org, lang)


# ---------------------------------------------------------------- init wizard
def run_init(out_base="org", lang="en", ask=input, say=print):
    """Ask a few questions, then write <out_base>.txt and <out_base>.json."""
    def q(prompt, default=None, conv=str):
        while True:
            ans = ask(f"{prompt}" + (f" [{default}]" if default not in (None, "") else "") + ": ").strip()
            if not ans and default is not None:
                ans = str(default)
            try:
                return conv(ans)
            except (ValueError, OrgSpecError) as e:
                say(t("i.bad", lang, e=e))

    say(t("i.intro", lang))
    names = q(t("i.bots", lang), "chief worker", lambda s: s.split() or (_ for _ in ()).throw(ValueError(s)))
    org = {"horizon_days": 7, "bots": [], "links": [], "groups": []}
    num = lambda s: parse_number(s, lang=lang)
    for i, name in enumerate(names):
        b = {"name": name, "context_tokens": q(t("i.ctx", lang, bot=name), "30k", num)}
        wakes = q(t("i.wakes", lang, bot=name), 3 if i == 0 else 0, num)
        if wakes:
            b["own_wakes_per_day"] = wakes
        b["work_rounds_per_wake"] = q(t("i.rounds", lang, bot=name), 1, num)
        if q(t("i.board", lang, bot=name), "n", lambda s: s.lower()[:1] in ("y", "n") and s.lower()[:1] or
             (_ for _ in ()).throw(ValueError(s))) == "y":
            b["reads_board_via_tool"] = True
        org["bots"].append(b)

    def link(s):
        p = s.split()
        if len(p) != 4 or p[0] not in names or p[1] not in names:
            raise ValueError(s)
        return {"from": p[0], "to": p[1], "messages_per_day": num(p[2]), "avg_tokens": num(p[3])}

    def group(s):
        p = s.split()
        mem = p[1].split(",") if len(p) == 4 else []
        if len(p) != 4 or not mem or any(m not in names for m in mem):
            raise ValueError(s)
        return {"name": p[0], "members": mem, "messages_per_day": num(p[2]), "avg_tokens": num(p[3])}

    for key, prompt, conv in (("links", "i.links", link), ("groups", "i.groups", group)):
        say(t(prompt, lang))
        while True:
            row = q(">", "", lambda s: s and conv(s) or None)
            if not row:
                break
            org[key].append(row)
    check_org(org, lang)
    txt, js = f"{out_base}.txt", f"{out_base}.json"
    with open(txt, "w", encoding="utf-8") as f:
        f.write(to_txt(org))
    with open(js, "w", encoding="utf-8") as f:
        json.dump(org, f, ensure_ascii=False, indent=2)
    say(t("i.done", lang, txt=os.path.basename(txt), json=os.path.basename(js)))
    return org
