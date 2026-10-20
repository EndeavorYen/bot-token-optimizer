# Token cost review — 2026-10-12 to 2026-10-19

## Bottom line

1. At the current pace your bots cost about **$158 per week** (≈ $22.61/day), as a token-priced equivalent. At this pace the quota lasts 3 days; lasting 7 days means getting down to about **$68 per week** (57% less).
2. **98% of it is grok-bot-default**, and 98% of all spend is re-reading context rather than writing output. A single bot, **monitor**, is 50% of all spend (schedule: every 30m).
3. The recommendations below save about **$72–$119 per week (45%–75%)**, counting each dollar once; with them the quota lasts about **5.5–12.0 days** (target 7).

Overall confidence: **medium**.

## Where the money goes

| Context | USD/week | Share | Wake-ups/day | Cost per wake-up | Typical context (p50 / p90) |
|---|---|---|---|---|---|
| grok-bot-default | $155 | 98% | 82.1 | $0.269 | 108,644 / 153,366 |
| grok-bot-automation | $3.47 | 2% | 24.1 | $0.021 | 30,033 / 31,741 |

### Who is inside grok-bot-automation, grok-bot-default

| Bot | Schedule | Wake-ups matched / fires | USD/week | Share | Cost per wake-up | Context p50 | Nothing to do |
|---|---|---|---|---|---|---|---|
| monitor | `every 30m` | 317 / 336 | $78.70 | 50% | $0.248 | 109,470 | 64% |
| digest | `at 09:00,18:00` | 14 / 14 | $6.35 | 4% | $0.452 | 90,978 | 0% |
| heartbeat | `every 1h` | 168 / 168 | $3.47 | 2% | $0.021 | 30,033 | 65% |
| grok-bot-default (unscheduled) | your messages and bot-to-bot pings | 242 | $69.77 | 44% | $0.287 | 109,084 | 0% |

Wake-ups are matched to the schedule that fired within 5 minutes before them, one per fire. “Nothing to do” = a single call writing at most 80 tokens.

### What the money pays for

| Component | USD/week | Share | Meaning |
|---|---|---|---|
| Uncached input | $127 | 80% | re-reading context the cache did not cover |
| Cached input | $28.45 | 18% | re-reading context at the cache discount |
| Output | $3.07 | 2% | what the bots actually write (incl. reasoning) |

### What changed

- Same window: 2026-10-19 up to 07:31 cost $9.44 vs $8.84 the day before over the same hours (+7%).
- grok-bot-default: $19.81 → $24.42 per day; wake-ups 76.2 → 88.0 per day, average context 111,785 → 107,840 tokens (first vs second half of the period).

## Recommendations

Ranked by dollars × confidence ÷ effort. Savings overlap, so they are not simply added up; the bottom line counts each dollar once.

| # | Action | Evidence | Saves / week | Confidence | Effort | Owner |
|---|---|---|---|---|---|---|
| 1 | **Run monitor every 2h instead of “every 30m”** | 64% of its 317 scheduled wake-ups made one call and wrote almost nothing (nothing to do), yet each re-read about 109,470 tokens for $0.248; it costs $78.70/week | $41.32–$59.02 (measured) | high | low | _to assign_ |
| 2 | **Hand over to a fresh chat at a set threshold per bot** | every wake-up re-reads the whole context; typical now → threshold: grok-bot-default (unscheduled) 109,084 → 100,000 (≈6.6 hand-overs/week), monitor 109,470 → 40,000 (≈5.8 hand-overs/week), digest 90,978 → 40,000 (≈2.0 hand-overs/week) | $24.91–$49.81 (modeled) | medium | low | _to assign_ |
| 3 | **Run heartbeat every 3h instead of “every 1h”** | 65% of its 168 scheduled wake-ups made one call and wrote almost nothing (nothing to do), yet each re-read about 30,033 tokens for $0.021; it costs $3.47/week | $1.62–$2.31 (measured) | high | low | _to assign_ |
| 4 | **Put the content in the notification instead of “go read the board”** | 19 wake-ups (3%) spend a full-context call just to fetch the work; mostly grok-bot-default | $1.34–$2.69 (measured) | medium | low | _to assign_ |
| 5 | **Stop breaking the prompt cache mid-wake-up** | calls after the first one in a wake-up still paid $16.53 for uncached input; mostly grok-bot-default | $2.47–$4.94 (measured) | medium | medium | _to assign_ |

**How**

1. Change the schedule of monitor; if some checks are urgent, keep a fast path (a direct ping when the event happens) instead of polling.
2. Put the rule in each bot's instructions so the bot hands over by itself when its context passes its threshold: a hand-over note (goals, decisions, open items, ≤ 3K tokens), then a new chat that starts from it. The savings already pay for every hand-over; thresholds are chosen so no context needs more than the weekly limit shown below.
3. Change the schedule of heartbeat; if some checks are urgent, keep a fast path (a direct ping when the event happens) instead of polling.
4. Inline small payloads; for large ones, name the exact section to read.
5. Keep fixed instructions first and unchanged; avoid editing or re-ordering earlier messages and tool lists.

### Hand-over thresholds compared

Each row prices one threshold for every context above it, including the cost of every hand-over (a hand-over note plus re-reading it in the new chat). ✓ = chosen: per context, the threshold that saves most with at most 7 hand-overs a week.

| Threshold (tokens) | Contexts above it | Hand-overs / week (all) | Most for one context | Saves / week |
|---|---|---|---|---|
| 40,000 ✓ | 3 | 28.2 | 20.4 | $65.94 |
| 60,000 | 3 | 16.7 | 12.1 | $59.25 |
| 80,000 | 3 | 12.0 | 8.6 | $51.32 |
| 100,000 ✓ | 2 | 8.5 | 6.6 | $40.49 |

### Paste to the main bot

The changes above as one message for the bot that manages your bots. Review it first; nothing changes until it is sent.

```text
Please make these changes to my bots and report the result of each one:
1. Change monitor's schedule from “every 30m” to “every 2h”; keep everything else as it is.
2. Add this rule to the instructions of monitor, digest, replacing any existing hand-over or reset rule: “When the current chat's context passes 40,000 tokens, write a hand-over note (goals, decisions made, open items, where the key files are; at most 3,000 tokens), save it, then continue in a new chat that starts from that note.”
3. Add this rule to the instructions of every other long-running bot, replacing any existing hand-over or reset rule: “When the current chat's context passes 100,000 tokens, write a hand-over note (goals, decisions made, open items, where the key files are; at most 3,000 tokens), save it, then continue in a new chat that starts from that note.”
4. Change heartbeat's schedule from “every 1h” to “every 3h”; keep everything else as it is.
5. Add to every bot's instructions: “When you notify another bot, include the content itself if it is a paragraph or less. For longer content, write it to a file and give the path and the exact section to read.”
Do not change anything else.
```

## Decisions and data needed

- 44% of spend ($70/week) in grok-bot-default is unscheduled: your messages and bot-to-bot pings. Next analysis step: map who pings whom (org.txt) and run bto org to see which links cost the most.
- Reaching $68/week needs about $19/week beyond these actions: choose between fewer wake-ups of grok-bot-default (unscheduled) (about 27% fewer) and a higher budget.
- 100% of calls have no Automation ID, so they are grouped by model (grok-bot-automation, grok-bot-default); each of those buckets mixes several bots. The schedules file attributes 56% of spend to 3 named bots; the rest stays unattributed. **How:** Get a per-bot split: export each bot separately (filter by bot) and run the report per file, or run recurring work as named automations so the id is filled.
- There is no chat / thread id, so calls from different chats are mixed: context size, growth and resets are blended across chats. **How:** Export with a chat id; otherwise read the context size of the main chats directly (one sample per chat is enough).
- Cost is plan-billed (Included / Free), so dollars are an equivalent priced from tokens, not a bill. Use them to compare options and as a proxy for quota burn. **How:** Nothing to fix; read the dollars as relative.

## How we will know it worked

| Measure | Now | Target | Check on |
|---|---|---|---|
| Spend per week | $158 | ≤ $68 | 2026-10-26 |
| Days the quota lasts | 3.0 days | ≥ 7.0 days | 2026-10-26 |
| Wake-ups that only say “OK” | 34% | ≤ 5% | 2026-10-26 |
| Share of input served from cache | 47% | ≥ 80% | 2026-10-26 |
| Typical context of grok-bot-default (p90) | 153,366 | ≤ 100,000 | 2026-10-26 |

Re-run this report on the same export window after the changes.

---

### Appendix — method and assumptions

- Period: 2026-10-12 08:00 – 2026-10-19 07:31 (UTC+08:00) (7.0 days, 1,236 calls). Weekly figures = period × 7 / days.
- Prices: grok-4.7 preset (input $2.0/M, cached $0.5/M, output $6.0/M; long-context tier from 200,000 tokens).
- Evidence: **measured** = dollars already spent on the pattern in this export (range = removing half to all of it); **modeled** = the cost model's projection (only trusted when calibration is within ±15%); **judgment** = a sensitivity range, not a forecast.
- Calibration: model $143.03 vs $157.83 for the period (-9.4%).
- A wake-up = consecutive calls of one context less than 120 s apart.
- Schedule matching: a wake-up starting from 60 s before to 300 s after a fire is given to that schedule, nearest first, one wake-up per fire. Nothing to do = one call, at most 80 output tokens. Frequency savings = measured cost per wake-up × wake-ups removed (range: 70–100%).
- Columns used: time=Date, bot=Automation ID, output=Output Tokens, model=Model, input=Input (w/ Cache Write) + Input (w/o Cache Write) + Cache Read, cached=Cache Read.
- Full per-context table: `bto analyze` on the same file.
