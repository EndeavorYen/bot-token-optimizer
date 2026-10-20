# Token cost review — 2026-10-01 to 2026-10-07

## Bottom line

1. At the current pace your bots cost about **$27 per week** (≈ $3.86/day), as a token-priced equivalent.
2. **32% of it is researcher**, and 96% of all spend is re-reading context rather than writing output.
3. The recommendations below save about **$7–$13 per week (25%–49%)**, counting each dollar once; your target of $18/week still needs about $2/week more, i.e. about 27% fewer wake-ups of researcher.

Overall confidence: **high**.

## Where the money goes

| Context | USD/week | Share | Wake-ups/day | Cost per wake-up | Typical context (p50 / p90) |
|---|---|---|---|---|---|
| researcher | $8.53 | 32% | 8.1 | $0.151 | 52,797 / 74,883 |
| chief | $7.93 | 29% | 6.1 | $0.187 | 91,436 / 132,693 |
| support/a | $4.36 | 16% | 6.1 | $0.103 | 80,707 / 97,360 |
| all others (3) | $6.16 | 23% | 20.2 | | |

### What the money pays for

| Component | USD/week | Share | Meaning |
|---|---|---|---|
| Uncached input | $17.57 | 65% | re-reading context the cache did not cover |
| Cached input | $8.33 | 31% | re-reading context at the cache discount |
| Output | $1.08 | 4% | what the bots actually write (incl. reasoning) |

### What changed

- Same window: 2026-10-07 up to 22:08 cost $4.84 vs $4.37 the day before over the same hours (+11%).
- chief: $0.82 → $1.45 per day; wake-ups 6.1 → 6.1 per day, average context 65,565 → 117,989 tokens (first vs second half of the period).
- researcher: $0.91 → $1.53 per day; wake-ups 8.1 → 8.1 per day, average context 38,727 → 66,788 tokens (first vs second half of the period).

## Recommendations

Ranked by dollars × confidence ÷ effort. Savings overlap, so they are not simply added up; the bottom line counts each dollar once.

| # | Action | Evidence | Saves / week | Confidence | Effort | Owner |
|---|---|---|---|---|---|---|
| 1 | **Hand over to a fresh chat whenever a context passes 60,000 tokens** | every wake-up re-reads the whole context (typical: chief ≈ 91,436, support/a ≈ 80,707 tokens); with a 60,000 threshold it would average about 35,500 | $3.36–$6.72 (modeled) | medium | low | _to assign_ |
| 2 | **Put the content in the notification instead of “go read the board”** | 56 wake-ups (20%) spend a full-context call just to fetch the work; mostly researcher | $1.87–$3.75 (measured) | medium | low | _to assign_ |
| 3 | **Stop breaking the prompt cache mid-wake-up** | calls after the first one in a wake-up still paid $2.58 for uncached input; mostly researcher, chief | $0.98–$1.96 (measured) | medium | medium | _to assign_ |
| 4 | **Stop wake-ups that only say “OK”** | 25 wake-ups (9% of all) made a single call with almost no output; mostly writer | $0.46–$0.91 (measured) | medium | low | _to assign_ |

**How**

1. When a context passes 60,000 tokens, ask the bot for a hand-over note (goals, decisions, open items, ≤ 3K tokens) and continue in a new chat that starts from it. A hand-over costs cents; it pays back within a few wake-ups.
2. Inline small payloads; for large ones, name the exact section to read.
3. Keep fixed instructions first and unchanged; avoid editing or re-ordering earlier messages and tool lists.
4. Tell bots not to acknowledge; batch notifications; only notify when the receiver must act.

### Paste to the main bot

The changes above as one message for the bot that manages your bots. Review it first; nothing changes until it is sent.

```text
Please make these changes to my bots and report the result of each one:
1. Add this rule to the instructions of chief, support/a, replacing any existing hand-over or reset rule: “When the current chat's context passes 60,000 tokens, write a hand-over note (goals, decisions made, open items, where the key files are; at most 3,000 tokens), save it, then continue in a new chat that starts from that note.”
2. Add to every bot's instructions: “When you notify another bot, include the content itself if it is a paragraph or less. For longer content, write it to a file and give the path and the exact section to read.”
3. Add to every bot's instructions: “Do not reply ‘OK’ or ‘got it’ to a notification. Send a message only when the receiver has to act.”
Do not change anything else.
```

## Decisions and data needed

- Reaching $18/week needs about $2/week beyond these actions: choose between fewer wake-ups of researcher (about 27% fewer) and a higher budget.

## How we will know it worked

| Measure | Now | Target | Check on |
|---|---|---|---|
| Spend per week | $27 | ≤ $18 | 2026-10-14 |
| Wake-ups that only say “OK” | 9% | ≤ 5% | 2026-10-14 |
| Wake-ups that start by fetching the work | 20% | ≤ 10% | 2026-10-14 |
| Share of input served from cache | 65% | ≥ 80% | 2026-10-14 |
| Typical context of researcher (p90) | 74,883 | ≤ 45,000 | 2026-10-14 |

Re-run this report on the same export window after the changes.

---

### Appendix — method and assumptions

- Period: 2026-10-01 00:04 – 2026-10-07 22:08 (UTC) (6.9 days, 479 calls). Weekly figures = period × 7 / days.
- Prices: grok-4.7 preset (input $2.0/M, cached $0.5/M, output $6.0/M; long-context tier from 200,000 tokens).
- Evidence: **measured** = dollars already spent on the pattern in this export (range = removing half to all of it); **modeled** = the cost model's projection (only trusted when calibration is within ±15%); **judgment** = a sensitivity range, not a forecast.
- Calibration: model $26.11 vs $26.67 for the period (-2.1%).
- A wake-up = consecutive calls of one context less than 120 s apart.
- Columns used: time=timestamp, bot=bot_name, chat=chat_id, input=input_tokens, cached=cached_input_tokens, output=output_tokens, reasoning=reasoning_tokens.
- Full per-context table: `bto analyze` on the same file.
