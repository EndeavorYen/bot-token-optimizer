"""Bilingual messages (English / 繁體中文) for reports, diagnostics and errors.

Language resolution: explicit --lang > BTO_LANG env > system locale (zh* → zh-TW) > en.
"""
import locale
import os

LANGS = ("en", "zh-TW")

M = {
    # ---------------------------------------------------------------- org report
    "org.title": ("Organization cost estimate ({days:g} days)", "組織成本評估（{days:g} 天）"),
    "org.total": ("Total: about {eq} input-token equivalents (about ${usd:,.2f}), of which output ${out_usd:,.2f}",
                  "總成本：約 {eq} token 等值（約 ${usd:,.2f}），其中 output 約 ${out_usd:,.2f}"),
    "org.header": ("| Bot | C now | C at end | Wake-ups/day | Calls/day | Share | USD equiv. |",
                   "| Bot | 目前 C | 期末 C | 喚醒/天 | 呼叫輪數/天 | 成本占比 | 等值美元 |"),
    "org.chain": ("Longest hand-off chain: {hops} hops ({chain})", "最長交接鏈：{hops} 跳（{chain}）"),
    "org.findings": ("Diagnosis and recommendations", "診斷與建議"),
    "org.overlap": ("(Estimated savings overlap and cannot be added up; verify a redesign with `compare`.)",
                    "（各項預估省量會互相重疊，不能直接相加；改組方案請用 compare 驗證總量。）"),
    "org.none": ("No obvious anti-patterns found.", "沒有發現明顯的反模式。"),
    "org.advice": ("Recommendation", "建議"),
    "org.saving": (" Estimated saving: about {eq} token equivalents.", "預估可省約 {eq} token 等值。"),
    "org.compare": ("Before: {a}   After: {b}   Change: {ratio:.2f}× ({delta})",
                    "改組前：{a}　改組後：{b}　變化：{ratio:.2f} 倍（{delta}）"),
    "org.after": ("After the redesign", "改組後"),

    "f.hotspot": ("{share:.0%} of total cost — the most expensive bot", "占總成本 {share:.0%}，是最貴的節點"),
    "a.hotspot": ("wake it less often (milestones and exceptions only), have its reports summarized, reset its context first",
                  "減少它的喚醒次數（只在里程碑或例外時通知）、讓下屬交摘要、優先重建它的上下文"),
    "f.relay": ("one upstream ({up}) and one downstream ({down}), passing on about as much as it receives",
                "只有一個上游（{up}）和一個下游（{down}），且往下傳的內容與收到的差不多長"),
    "a.relay": ("likely a pure relay: let upstream and downstream hand off directly, or make it summarize",
                "疑似純傳話：讓上下游直接交接，或改成壓縮摘要後再往下傳"),
    "f.span": ("receives messages directly from {n} sources, above the span limit of {cap}",
               "直接接收 {n} 個來源的訊息，超過控制幅度 {cap}"),
    "a.span": ("add a summarizing leader, or have the sources merge their reports first",
               "加一層會彙整的 leader，或讓來源先合併回報"),
    "f.board": ("reads the board via a tool after each notification: one extra full-context call per notification",
                "被通知後用 tool 讀 board，每次通知多一輪完整上下文"),
    "a.board": ("put small content in the message; for large content, name the section to read",
                "小內容直接放在訊息裡；大內容在通知中註明要讀哪一段"),
    "f.reset": ("context now {c} (about {c_end} at the end), about {c_new} after a reset",
                "目前上下文 {c}（期末約 {c_end}），重建後約 {c_new}"),
    "f.reset.cycle": ("suggested hand-over about every {d:.1f} days", "建議約每 {d:.1f} 天重建一次"),
    "a.reset": ("have it write a hand-over summary and continue in a fresh chat or a new bot",
                "請它寫交接摘要，開新 chat 或新 bot 接手"),
    "r.long_now": ("context has reached the {thr} long-context tier: the unit price doubles",
                   "上下文已達 {thr} 長上下文計價門檻，單價翻倍"),
    "r.soft_now": ("context is above the quality soft cap of {cap}", "上下文超過品質軟上限 {cap}"),
    "r.breakeven": ("{n:g} expected wake-ups exceed the break-even of {b:.1f}", "預計喚醒 {n:g} 次，超過回本次數 {b:.1f} 次"),
    "r.long_soon": ("at the current growth rate it will cross the {thr} price tier within the period",
                    "依目前成長速度，期間內會越過 {thr} 計價門檻"),
    "r.soft_soon": ("it will exceed the quality soft cap of {cap} within the period", "期間內會超過品質軟上限 {cap}"),
    "f.long": ("about {c_end} by the end, close to the {thr} price tier", "期末上下文約 {c_end}，接近 {thr} 計價門檻"),
    "a.long": ("hand over before crossing the tier", "在越過門檻前交接重建"),
    "f.ack": ("{src} → {dst}: {n:g} messages/day averaging {tok:g} tokens — likely “OK / got it” acknowledgements",
              "{src} → {dst} 每天 {n:g} 則、平均 {tok:g} token，疑似「收到/OK」確認訊息"),
    "a.ack": ("forbid pure acknowledgements in the bots' instructions; batch notifications",
              "在 bot 指令中禁止純確認訊息；合併通知"),
    "f.group": ("group messages average {tok:g} tokens, and each lands in the context of all {n} members",
                "群組平均每則 {tok:g} token，每則都進入 {n} 個成員的上下文"),
    "a.group": ("send long content one-to-one or via shared files; keep one-line status in the group",
                "長內容改走一對一或共享檔案，群組只留一行狀態"),
    "f.latency": ("the longest hand-off chain has {hops} hops: {chain}", "最長交接鏈有 {hops} 跳：{chain}"),
    "a.latency": ("run independent steps in parallel and let bots hand off directly; every hop is another round of waiting",
                  "讓可平行的步驟同時進行、讓下游直接交接；每多一跳就多一輪等待"),

    # ---------------------------------------------------------------- validation
    "v.title": ("The organization description has problems:", "組織描述有問題："),
    "v.no_bots": ("'bots' must be a non-empty list", "'bots' 必須是非空的清單"),
    "v.bot_obj": ("bots[{i}] must be an object", "bots[{i}] 必須是物件"),
    "v.missing": ("{where}: missing required field '{field}'", "{where}：缺少必要欄位 '{field}'"),
    "v.dup": ("bot name '{name}' is used more than once", "bot 名稱 '{name}' 重複"),
    "v.num": ("{where}: '{field}' must be a number ≥ 0 (got {val!r})", "{where}：'{field}' 必須是 ≥ 0 的數字（目前是 {val!r}）"),
    "v.ratio": ("{where}: '{field}' must be between 0 and 1 (got {val!r})", "{where}：'{field}' 必須介於 0 和 1 之間（目前是 {val!r}）"),
    "v.unknown_bot": ("{where}: refers to a bot that does not exist: '{name}'", "{where}：參照了不存在的 bot：'{name}'"),
    "v.wake": ("{where}: 'wake' must be 'addressed' or 'all'", "{where}：'wake' 必須是 'addressed' 或 'all'"),
    "v.members": ("{where}: 'members' must be a non-empty list of bot names", "{where}：'members' 必須是非空的 bot 名稱清單"),
    "v.unknown_key": ("{where}: unknown field '{field}' (typo?) — ignored", "{where}：不認得的欄位 '{field}'（打錯字？），已忽略"),

    # ---------------------------------------------------------------- org.txt
    "t.syntax": ("line {n}: cannot parse: {line}", "第 {n} 行：無法解析：{line}"),
    "t.number": ("line {n}: not a number: {tok}", "第 {n} 行：不是數字：{tok}"),
    "t.option": ("line {n}: unknown option '{opt}'", "第 {n} 行：不認得的選項 '{opt}'"),

    # ---------------------------------------------------------------- usage analysis
    "u.title": ("Usage analysis ({start} – {end}, about {days:.1f} days, {rows:,} calls)",
                "Usage 分析（{start} ～ {end}，約 {days:.1f} 天，{rows:,} 筆呼叫）"),
    "u.total": ("Total cost for the period (equivalent): about ${usd:,.2f}", "期間總成本（等值）：約 ${usd:,.2f}"),
    "u.header": ("| Context | Share | Calls | Wake-ups/day | Calls/wake-up | C now | C growth/day | Resets seen | Cache hits | USD equiv. |",
                 "| 上下文 | 占比 | 呼叫 | 喚醒/天 | 輪數/喚醒 | 目前 C | C 成長/天 | 觀察到的重建 | 快取命中 | 等值美元 |"),
    "u.unit_note": ("Each row is one context (bot, or bot/chat when a bot has several chats): every chat keeps its own context.",
                    "每一列是一份上下文（bot；同一個 bot 有多個 chat 時為 bot/chat），因為每個 chat 各自累積上下文。"),
    "u.calib": ("Model calibration: from the measured parameters the model reproduces ${model:,.2f} for the period vs ${actual:,.2f} actual, error {err:+.1%}.",
                "模型校正：用實測參數回推期間成本 ${model:,.2f}，實際 ${actual:,.2f}，誤差 {err:+.1%}。"),
    "u.calib_ok": ("Within ±15%: the model can be used to estimate the effect of a redesign.",
                   "誤差在 ±15% 內，模型可用於推估改組效果。"),
    "u.calib_bad": ("Error is large: treat model estimates as indicative only (common causes: unsuitable wake-up gap, non-linear growth).",
                    "誤差偏大，推估結果僅供參考（常見原因：喚醒切分間隔不合適、成長不是線性）。"),
    "u.findings": ("Findings", "發現"),
    "u.none": ("No obvious anomalies found.", "沒有發現明顯的異常模式。"),
    "u.related": ("(related cost about ${usd:,.2f})", "（相關成本約 ${usd:,.2f}）"),
    "u.footer": ("Note: wake-ups are estimated from gaps between consecutive calls of the same context; low-output and read-then-work are heuristics — check them against the actual conversations.",
                 "註：喚醒是用「同一份上下文連續呼叫間隔」切分的估計值；低產出與先讀再做都是啟發式判斷，請對照實際對話確認。"),
    "u.mapping": ("Column mapping: {m}", "欄位對應：{m}"),
    "u.skipped": ("skipped {n} unparseable rows", "略過 {n} 筆無法解析的資料列"),
    "u.no_bot": ("Note: no bot column in the CSV; ", "注意：CSV 沒有 bot 欄位，"),
    "u.use_chat": ("grouping by the chat column instead.", "改用 chat 欄位分組。"),
    "u.all_one": ("all calls are analyzed together.", "所有呼叫合併分析。"),
    "u.no_cache": ("Note: no cached-token column; cost is estimated as fully uncached (too high).",
                   "注意：CSV 沒有快取 token 欄位，成本以全部未快取估算（偏高）。"),
    "u.ack": ("{n} wake-ups ({p:.0%}) make one call with ≤ {thr} output tokens — likely “OK” replies or nothing to do",
              "{n} 次喚醒（{p:.0%}）只呼叫一輪且輸出 ≤ {thr} token，疑似「收到/OK」或無事可做"),
    "u.ack.a": ("forbid pure acknowledgements, batch notifications, wake only when action is needed",
                "禁止純確認訊息、合併通知、只在需要行動時喚醒"),
    "u.read": ("{n} wake-ups ({p:.0%}) produce almost nothing in the first call and only then start working — likely reading a board via a tool",
               "{n} 次喚醒（{p:.0%}）第一輪幾乎沒有輸出、之後才開始工作，疑似先用 tool 讀 board"),
    "u.read.a": ("put small content in the notification; for large content, name the section to read",
                 "小內容直接放進通知；大內容在通知中指明要讀的段落"),
    "u.long": ("{n} calls had more than {thr} tokens of context and were billed at the long-context price",
               "{n} 次呼叫的上下文超過 {thr}，被收取長上下文價格"),
    "u.long.a": ("hand over before crossing the tier", "在越過門檻前交接重建"),
    "u.growth": ("context about {c}, growing about {g}/day, crosses {thr} in about {d:.1f} days",
                 "上下文約 {c}，每天成長約 {g}，約 {d:.1f} 天後越過 {thr}"),
    "u.growth.a": ("schedule a hand-over", "排定交接重建"),
    "u.cache": ("cache hit rate only {p:.0%}", "快取命中率只有 {p:.0%}"),
    "u.cache.a": ("avoid editing early context; long gaps between wake-ups expire the cache; keep fixed content first",
                  "避免修改早期上下文、喚醒間隔過長會讓快取失效；固定內容放最前面"),
    "u.hotspot": ("{p:.0%} of total cost", "占總成本 {p:.0%}"),
    "u.hotspot.a": ("wake it less often, have upstream bots summarize, reset its context first",
                    "減少它的喚醒次數、讓上游交摘要、優先重建它的上下文"),
    "u.skeleton_note": ("own_wakes_per_day currently counts all wake-ups and growth_per_day is the measured growth. After adding links, subtract the wake-ups caused by messages from own_wakes_per_day and delete growth_per_day so the model computes growth itself.",
                        "own_wakes_per_day 目前包含所有喚醒；growth_per_day 是實測成長。補上 links 後，請把由訊息造成的喚醒從 own_wakes_per_day 扣掉，並刪除 growth_per_day 讓模型自行推算。"),
    "u.err_missing": ("Required columns not found: {missing}. CSV columns: {cols}. Use --map, e.g. --map time=Date",
                      "找不到必要欄位 {missing}。CSV 欄位為：{cols}。請用 --map 指定，例如 --map time=日期"),
    "u.err_map": ("--map column {col!r} is not in the CSV; available: {cols}", "--map 指定的欄位 {col!r} 不在 CSV 中；可用欄位：{cols}"),
    "u.split_input": ("Note: the prompt is split over three columns (cache write, uncached, cache read); input = their sum, cached = Cache Read.",
                      "注意：prompt 分在三欄（寫入快取、未快取、讀快取）；input 取三欄加總，cached 取 Cache Read。"),
    "u.cloud_agent": ("excluded {n} rows with a {col} (one-shot cloud runs, not persistent bots)",
                      "排除 {n} 筆有 {col} 的資料列（一次性的雲端執行，不是常駐 bot）"),
    "u.cost_text": ("Note: {n} rows have a non-numeric {col!r} (e.g. Included / Free), so cost is priced from tokens with the pricing preset and calibration is against that estimate, not a bill.",
                    "注意：{n} 筆的 {col!r} 不是數字（例如 Included／Free），成本改用價格設定從 token 推算；校正比的是這個推算值，不是帳單。"),
    "u.err_num": ("column {col!r} has a non-numeric value {v!r}", "欄位 {col!r} 有非數字的值 {v!r}"),
    "u.err_empty": ("The CSV has no usable rows", "CSV 沒有可用的資料列"),
    "u.err_time": ("cannot parse time: {v!r}", "無法解析時間：{v!r}"),

    # ---------------------------------------------------------------- init wizard
    "i.intro": ("Answer a few questions to describe your bots. Press Enter to accept the [default].",
                "回答幾個問題來描述你的 bot。直接按 Enter 採用 [預設值]。"),
    "i.bots": ("Bot names, separated by spaces", "bot 名稱，用空白分隔"),
    "i.ctx": ("  {bot}: current context size in tokens (a fresh chat ≈ 10k, a long-running one 100k+)",
              "  {bot}：目前上下文大小（token；剛開的 chat 約 10k，跑很久的 100k 以上）"),
    "i.wakes": ("  {bot}: wake-ups per day NOT caused by other bots (you, schedules)", "  {bot}：每天不是由其他 bot 造成的喚醒次數（你、排程）"),
    "i.rounds": ("  {bot}: tool-call rounds of its own work per wake-up", "  {bot}：每次喚醒為自己工作做幾輪工具呼叫"),
    "i.board": ("  {bot}: after a notification, does it first read a board/shared file? (y/n)",
                "  {bot}：收到通知後會先去讀 board 或共享檔案嗎？(y/n)"),
    "i.links": ("Who sends work to whom? One per line: FROM TO MESSAGES_PER_DAY TOKENS_PER_MESSAGE (empty line to finish)",
                "誰把工作交給誰？一行一條：來源 目標 每天幾則 每則幾個token（空行結束）"),
    "i.groups": ("Group chats? One per line: NAME MEMBER1,MEMBER2,... MESSAGES_PER_DAY TOKENS_PER_MESSAGE (empty line to finish)",
                 "有群組嗎？一行一個：名稱 成員1,成員2,... 每天幾則 每則幾個token（空行結束）"),
    "i.bad": ("  Could not read that line, please retry: {e}", "  這行看不懂，請再輸入一次：{e}"),
    "i.done": ("Wrote {txt} (editable) and {json}. Next: bto org {json}", "已寫入 {txt}（可直接編輯）和 {json}。下一步：bto org {json}"),
}


def resolve_lang(lang=None):
    if lang:
        return "zh-TW" if lang.lower().startswith("zh") else "en"
    env = os.environ.get("BTO_LANG")
    if env:
        return resolve_lang(env)
    try:
        loc = locale.getlocale()[0] or os.environ.get("LANG", "")
    except ValueError:
        loc = os.environ.get("LANG", "")
    return "zh-TW" if str(loc).lower().startswith("zh") else "en"


def t(key, lang="en", **kw):
    en, zh = M[key]
    s = zh if lang == "zh-TW" else en
    return s.format(**kw) if kw else s


def sep(lang):
    """Separator between clauses."""
    return "；" if lang == "zh-TW" else "; "
