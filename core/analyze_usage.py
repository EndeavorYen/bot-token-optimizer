#!/usr/bin/env python3
"""analyze_usage — 分析 Grok Bot 匯出的 usage CSV，找出 token 消耗的來源。

做的事：
  1. 自動對應欄位（時間、bot、input/cached/output/reasoning token、成本）
  2. 依 bot 統計：呼叫數、喚醒次數、每次喚醒輪數、上下文大小 C、快取命中率
  3. 偵測：上下文成長趨勢與越過門檻的時間、低產出喚醒（疑似「收到/OK」）、
     「先讀再做」型喚醒（疑似讀 board）、長上下文計價、快取命中率偏低
  4. 產出 org.json 骨架，交給 orgopt.py 做組織模擬

只用 Python 標準函式庫。欄位名稱對不上時，用 --map 欄位=CSV欄名 指定。
"""
import argparse
import csv
import json
import math
import os
import statistics
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import orgopt  # noqa: E402

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


# ---------------------------------------------------------------- 讀檔
def _norm(s):
    return s.strip().lower().replace(" ", "_").replace("-", "_")


def map_columns(header, overrides=None):
    norm = {_norm(h): h for h in header}
    mapping = {}
    for field, names in ALIASES.items():
        for n in names:
            if n in norm:
                mapping[field] = norm[n]
                break
    for k, v in (overrides or {}).items():
        if v not in header:
            raise ValueError(f"--map 指定的欄位 {v!r} 不在 CSV 中；可用欄位：{header}")
        mapping[k] = v
    missing = [f for f in REQUIRED if f not in mapping]
    if missing:
        raise ValueError(f"找不到必要欄位 {missing}。CSV 欄位為：{header}。請用 --map 指定，例如 --map time=日期")
    return mapping


def parse_time(v):
    v = str(v).strip()
    if not v:
        raise ValueError("空的時間欄位")
    try:
        x = float(v)
        if x > 1e12:      # 毫秒
            x /= 1000
        return datetime.fromtimestamp(x, tz=timezone.utc)
    except ValueError:
        pass
    s = v.replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%Y-%m-%d"):
            try:
                d = datetime.strptime(v, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"無法解析時間：{v!r}")
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def _num(v):
    v = str(v).strip().replace(",", "")
    if v == "" or v.lower() in ("nan", "null", "none", "-"):
        return 0.0
    return float(v)


def load_rows(path, overrides=None, cached_separate=False):
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(8192)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        mapping = map_columns(reader.fieldnames or [], overrides)
        rows, skipped = [], 0
        for raw in reader:
            try:
                r = {"time": parse_time(raw[mapping["time"]]),
                     "input": _num(raw[mapping["input"]])}
            except (ValueError, KeyError):
                skipped += 1
                continue
            r["bot"] = (raw.get(mapping["bot"], "") or "").strip() if "bot" in mapping else ""
            if not r["bot"]:
                r["bot"] = (raw.get(mapping["chat"], "") or "").strip() if "chat" in mapping else ""
            r["bot"] = r["bot"] or "(全部)"
            r["chat"] = (raw.get(mapping["chat"], "") or "").strip() if "chat" in mapping else ""
            for k in ("cached", "output", "reasoning", "cost"):
                r[k] = _num(raw.get(mapping[k], 0)) if k in mapping else 0.0
            if cached_separate:          # 快取 token 不包含在 input 內 → 合併
                r["input"] += r["cached"]
            r["cached"] = min(r["cached"], r["input"])
            rows.append(r)
    rows.sort(key=lambda r: r["time"])
    return rows, mapping, skipped


# ---------------------------------------------------------------- 分析
def _fit(xs, ys):
    """最小平方法直線：回傳 (斜率, 截距)。資料太少時斜率為 0。"""
    n = len(xs)
    my = sum(ys) / n
    if n < 3:
        return 0.0, my
    mx = sum(xs) / n
    den = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 0.0
    return slope, my - slope * mx


def sessionize(rows, gap_seconds):
    """同一 bot 內，間隔不超過 gap 的連續呼叫視為同一次喚醒。"""
    wakes, cur = [], []
    for r in rows:
        if cur and (r["time"] - cur[-1]["time"]).total_seconds() > gap_seconds:
            wakes.append(cur)
            cur = []
        cur.append(r)
    if cur:
        wakes.append(cur)
    return wakes


def analyze(rows, pricing, gap_seconds=120, ack_output=30, read_first_output=80,
            soft_cap=None, has_cost=False):
    thr = pricing.get("long_context_threshold") or math.inf
    if not rows:
        raise ValueError("CSV 沒有可用的資料列")
    t0, t1 = rows[0]["time"], rows[-1]["time"]
    span_days = max((t1 - t0).total_seconds() / 86400, 1 / 24)
    by_bot = {}
    for r in rows:
        by_bot.setdefault(r["bot"], []).append(r)

    bots, total_usd = [], 0.0
    for name, rs in by_bot.items():
        usd = sum(r["cost"] for r in rs) if has_cost else sum(
            orgopt.call_cost_usd(r["input"], r["cached"], r["output"] + r["reasoning"], pricing) for r in rs)
        input_usd = sum(orgopt.call_cost_usd(r["input"], r["cached"], 0, pricing) for r in rs)
        total_usd += usd
        wakes = sessionize(rs, gap_seconds)
        first_inputs = [w[0]["input"] for w in wakes]
        days = [(w[0]["time"] - t0).total_seconds() / 86400 for w in wakes]
        slope, intercept = _fit(days, first_inputs)            # 每天成長 token
        recent = first_inputs[-max(3, len(first_inputs) // 5):]
        c_now = statistics.median(recent)
        inp = sum(r["input"] for r in rs)
        cached = sum(r["cached"] for r in rs)
        out = sum(r["output"] for r in rs)
        reasoning = sum(r["reasoning"] for r in rs)
        short_wakes = [w for w in wakes if len(w) == 1 and w[0]["output"] <= ack_output]
        short_usd = sum(orgopt.call_cost_usd(w[0]["input"], w[0]["cached"], w[0]["output"], pricing)
                        for w in short_wakes)
        read_first = [w for w in wakes if len(w) >= 2 and w[0]["output"] <= read_first_output]
        read_first_usd = sum(orgopt.call_cost_usd(w[0]["input"], w[0]["cached"], w[0]["output"], pricing)
                             for w in read_first)
        long_calls = [r for r in rs if r["input"] >= thr]
        long_extra = sum(orgopt.call_cost_usd(r["input"], r["cached"], r["output"], pricing)
                         - orgopt.call_cost_usd(r["input"], r["cached"], r["output"],
                                                dict(pricing, long_context_threshold=None)) for r in long_calls)

        def days_to(target):
            if c_now >= target:
                return 0.0
            return (target - c_now) / slope if slope > 0 else math.inf

        bots.append({
            "bot": name, "calls": len(rs), "wakes": len(wakes),
            "wakes_per_day": len(wakes) / span_days,
            "rounds_per_wake": len(rs) / len(wakes),
            "C_now": c_now, "C_start": max(intercept, 0.0), "C_max": max(r["input"] for r in rs),
            "input_usd": input_usd,
            "C_growth_per_day": slope,
            "days_to_long_context": days_to(thr),
            "days_to_soft_cap": days_to(soft_cap) if soft_cap else None,
            "cache_hit": cached / inp if inp else 0.0,
            "input_tokens": inp, "output_tokens": out, "reasoning_tokens": reasoning,
            "output_per_wake": (out + reasoning) / len(wakes),
            "usd": usd,
            "short_wakes": len(short_wakes), "short_wake_usd": short_usd,
            "read_first_wakes": len(read_first), "read_first_usd": read_first_usd,
            "long_context_calls": len(long_calls), "long_context_extra_usd": long_extra,
        })
    for b in bots:
        b["share"] = b["usd"] / total_usd if total_usd else 0.0
    bots.sort(key=lambda b: -b["usd"])

    findings = []
    n = len(bots)
    for b in bots:
        if n >= 3 and b["share"] >= max(0.30, 1.5 / n):
            findings.append(("hotspot", b["bot"], f"占總成本 {b['share']:.0%}", None,
                             "減少它的喚醒次數、讓上游交摘要、優先重建它的上下文"))
        if b["short_wakes"] and b["short_wakes"] / b["wakes"] >= 0.15:
            findings.append(("ack", b["bot"],
                             f"{b['short_wakes']} 次喚醒（{b['short_wakes'] / b['wakes']:.0%}）只呼叫一輪且輸出 ≤ {ack_output} token，疑似「收到/OK」或無事可做",
                             b["short_wake_usd"], "禁止純確認訊息、合併通知、只在需要行動時喚醒"))
        if b["read_first_wakes"] and b["read_first_wakes"] / b["wakes"] >= 0.3:
            findings.append(("read_first", b["bot"],
                             f"{b['read_first_wakes']} 次喚醒（{b['read_first_wakes'] / b['wakes']:.0%}）第一輪幾乎沒有輸出、之後才開始工作，疑似先用 tool 讀 board",
                             b["read_first_usd"], "小內容直接放進通知；大內容在通知中指明要讀的段落"))
        if b["long_context_calls"]:
            findings.append(("long_context", b["bot"],
                             f"{b['long_context_calls']} 次呼叫的上下文超過 {thr:,.0f}，被收取長上下文價格",
                             b["long_context_extra_usd"], "在越過門檻前交接重建"))
        elif b["days_to_long_context"] != math.inf and b["days_to_long_context"] <= 14:
            findings.append(("growth", b["bot"],
                             f"上下文約 {b['C_now']:,.0f}，每天成長約 {b['C_growth_per_day']:,.0f}，約 {b['days_to_long_context']:.1f} 天後越過 {thr:,.0f}",
                             None, "排定交接重建"))
        if b["input_tokens"] and b["cache_hit"] < 0.5 and b["calls"] >= 10:
            findings.append(("cache", b["bot"], f"快取命中率只有 {b['cache_hit']:.0%}", None,
                             "避免修改早期上下文、喚醒間隔過長會讓快取失效；固定內容放最前面"))
    findings.sort(key=lambda f: -(f[3] or 0))
    calib = calibrate(bots, span_days, pricing)
    return {"calibration": calib,"period_start": t0.isoformat(), "period_end": t1.isoformat(), "span_days": span_days,
            "total_usd": total_usd, "rows": len(rows), "bots": bots,
            "findings": [dict(zip(("type", "bot", "detail", "usd", "advice"), f)) for f in findings]}


def calibrate(bots, span_days, pricing):
    """用實測參數讓 orgopt 回推同一期間的 input 成本，和實際值比對，檢查模型是否可信。"""
    org = {"horizon_days": span_days, "bots": [{
        "name": b["bot"], "context_tokens": b["C_start"],
        "own_wakes_per_day": b["wakes_per_day"],
        "work_rounds_per_wake": max(b["rounds_per_wake"] - 1, 0),
        "cache_hit_ratio": b["cache_hit"], "growth_per_day": b["C_growth_per_day"],
        "output_tokens_per_wake": 0} for b in bots]}
    model_usd = orgopt.evaluate_org(org, pricing)["total_usd"]
    actual_usd = sum(b["input_usd"] for b in bots)
    err = (model_usd - actual_usd) / actual_usd if actual_usd else math.nan
    return {"model_input_usd": model_usd, "actual_input_usd": actual_usd, "error": err}


def org_skeleton(res, horizon_days=7):
    """依實測值產出 org.json 骨架。links 需由使用者或顧問 bot 補上。"""
    bots = []
    for b in res["bots"]:
        bots.append({
            "name": b["bot"],
            "context_tokens": round(b["C_now"]),
            "base_tokens": round(min(b["C_now"], 8000)),
            "own_wakes_per_day": round(b["wakes_per_day"], 2),
            "work_rounds_per_wake": round(max(b["rounds_per_wake"] - 1, 0), 2),
            "output_tokens_per_wake": round(b["output_per_wake"]),
            "cache_hit_ratio": round(b["cache_hit"], 3),
            "growth_per_day": round(b["C_growth_per_day"]),
        })
    return {"horizon_days": horizon_days,
            "_note": "own_wakes_per_day 目前包含所有喚醒；growth_per_day 是實測成長。補上 links 後，請把由訊息造成的喚醒從 own_wakes_per_day 扣掉，並刪除 growth_per_day 讓模型自行推算。",
            "bots": bots, "links": [], "groups": []}


def report_md(res):
    L = [f"## Usage 分析（{res['period_start'][:16]} ～ {res['period_end'][:16]}，約 {res['span_days']:.1f} 天，{res['rows']:,} 筆呼叫）", "",
         f"期間總成本（等值）：約 ${res['total_usd']:,.2f}", "",
         "| Bot | 占比 | 呼叫 | 喚醒/天 | 輪數/喚醒 | 目前 C | C 成長/天 | 快取命中 | 等值美元 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for b in res["bots"]:
        L.append(f"| {b['bot']} | {b['share']:.0%} | {b['calls']} | {b['wakes_per_day']:.1f} | {b['rounds_per_wake']:.1f} | "
                 f"{b['C_now']:,.0f} | {b['C_growth_per_day']:,.0f} | {b['cache_hit']:.0%} | ${b['usd']:,.2f} |")
    c = res["calibration"]
    L += ["", f"模型校正：用實測參數回推期間 input 成本 ${c['model_input_usd']:,.2f}，實際 ${c['actual_input_usd']:,.2f}，"
              f"誤差 {c['error']:+.1%}。" + ("誤差在 ±15% 內，模型可用於推估改組效果。" if abs(c["error"]) <= 0.15
                                          else "誤差偏大，推估結果僅供參考（常見原因：喚醒切分間隔不合適、成長不是線性）。"),
          "", "## 發現", ""]
    if not res["findings"]:
        L.append("沒有發現明顯的異常模式。")
    for i, f in enumerate(res["findings"], 1):
        s = f"{i}. **[{f['type']}] {f['bot']}**：{f['detail']}。建議：{f['advice']}。"
        if f["usd"]:
            s += f"（相關成本約 ${f['usd']:,.2f}）"
        L.append(s)
    L += ["", "註：喚醒是用「同一 bot 連續呼叫間隔」切分的估計值；低產出與先讀再做都是啟發式判斷，請對照實際對話確認。"]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="分析 Grok Bot usage CSV")
    ap.add_argument("csv")
    ap.add_argument("--pricing", help="價格設定檔路徑（預設 pricing/grok-4.7.json）")
    ap.add_argument("--platform", help="平台設定，例如 adapters/grok-bot/platform.json")
    ap.add_argument("--map", action="append", default=[], help="欄位=CSV欄名，例如 --map bot=Bot名稱")
    ap.add_argument("--cached-separate", action="store_true", help="CSV 的快取 token 不包含在 input 內")
    ap.add_argument("--gap", type=float, default=120, help="切分喚醒的間隔秒數（預設 120）")
    ap.add_argument("--soft-cap", type=float, help="上下文品質軟上限")
    ap.add_argument("--org-out", help="輸出 org.json 骨架的路徑")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    overrides = dict(m.split("=", 1) for m in a.map)
    plat = orgopt.load_platform(a.platform) if a.platform else {}
    pricing = orgopt.load_pricing(a.pricing or plat.get("pricing_path"))
    rows, mapping, skipped = load_rows(a.csv, overrides, a.cached_separate)
    res = analyze(rows, pricing, a.gap, soft_cap=a.soft_cap, has_cost="cost" in mapping)
    res["column_mapping"] = mapping
    res["skipped_rows"] = skipped
    if a.org_out:
        with open(a.org_out, "w", encoding="utf-8") as f:
            json.dump(org_skeleton(res), f, ensure_ascii=False, indent=2)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"欄位對應：{mapping}" + (f"；略過 {skipped} 筆無法解析的資料列" if skipped else ""))
        if "bot" not in mapping:
            print("注意：CSV 沒有 bot 欄位，" + ("改用 chat 欄位分組。" if "chat" in mapping else "所有呼叫合併分析。"))
        if "cached" not in mapping:
            print("注意：CSV 沒有快取 token 欄位，成本以全部未快取估算（偏高）。")
        print()
        print(report_md(res))


if __name__ == "__main__":
    sys.exit(main())
