# Advisor bot: a sample engagement

**English** · [繁體中文](advisor-demo.zh-TW.md)

This is what working with the advisor bot looks like, end to end, on the most common Grok Bot situation: the quota runs out early and the usage export cannot tell the bots apart. The advisor follows the playbook in [`ADVISOR_BOT.en.md`](../adapters/grok-bot/ADVISOR_BOT.en.md): frame, **investigate**, diagnose, recommend, plan and verify. Every number below is real output from this repo's tools on synthetic data. Reproduce it:

```bash
python3 samples/make_grok_export.py usage-events.csv
bto report usage-events.csv --platform grok-bot --tz +08:00 --quota-days 3 \
    --schedules samples/grok_schedules.txt --out memo.md
```

The full memo it produces: [sample-memo-grok.en.md](sample-memo-grok.en.md).

---

**Client:** My goal is to make 100% of the quota last 7 days. Right now it's gone in about 3. *(attaches the usage export)*

**Advisor** *(no questions yet: it reads every bot's settings and writes their schedules to `schedules.txt`: monitor every 30 minutes, digest at 09:00 and 18:00, heartbeat every hour. It also notes monitor's current hand-over rule, then runs `bto report` with the schedules and a budget of 3/7 of the current pace)*:

> At this pace your bots use about **$158 a week**; to make the quota last 7 days we need about **$68**. **Half of all spend is one bot: monitor.** It wakes every 30 minutes, re-reads a 110K-token chat each time, and finds nothing to do in 64% of those checks. Its chat is only summarized when the platform does it automatically, at about 165K tokens.
>
> | # | Change | Saves / week | How sure |
> |---|---|---|---|
> | 1 | **monitor: every 30 min → every 2 h** | $41–59 | measured, high |
> | 2 | **Hand over to a fresh chat: monitor and digest at 40K tokens, your own chats at 100K**, each about once a day or less (today: only the platform's automatic summary) | $25–50 | modeled, medium |
> | 3 | **heartbeat: every 1 h → every 3 h** (65% of its checks are idle) | $2 | measured, high |
> | 4 | Put short content in the ping instead of "go read the board" | $2–4 | measured, medium |
>
> Together about **$72–120 a week**, counting each dollar once, so the quota would last about **5.5–12 days**. At the low end that is still short of 7. The rest sits in your own chats and bot-to-bot pings ($70/week), and I am mapping who pings whom next.
>
> Change request for the main bot:
>
> ```text
> Please make these changes to my bots and report the result of each one:
> 1. Change monitor's schedule from "every 30m" to "every 2h"; keep everything else as it is.
> 2. Add this rule to the instructions of monitor and digest, replacing any existing hand-over or reset rule: "When the current chat's context passes 40,000 tokens, write a hand-over note (goals, decisions made, open items, where the key files are; at most 3,000 tokens), save it, then continue in a new chat that starts from that note."
> 3. Add the same rule, with 100,000 tokens, to the instructions of every bot I chat with directly.
> 4. Change heartbeat's schedule from "every 1h" to "every 3h"; keep everything else as it is.
> 5. Add to every bot's instructions: "When you notify another bot, include the content itself if it is a paragraph or less. For longer content, write it to a file and give the path and the exact section to read."
> Do not change anything else.
> ```
>
> Shall I send it? One decision for you: with monitor every 2 hours, an incident can wait up to 2 hours to be noticed. Is that acceptable, or should the alerting bot ping monitor directly when something breaks?

**Client:** Send it. Two hours is fine.

**Advisor** *(sends the request to the main bot and relays its report)*:

> Done: all five changes are in place. **Check on 26 October:** weekly spend ≤ $68, the quota lasting at least 7 days, and cache share ≥ 80%. Export the same seven days again then and I will compare like for like. Meanwhile I will map the unscheduled $70/week. Next time, start a fresh chat with me; it keeps my own costs down.

---

## What makes this a consultant and not a calculator

- **It investigates before it asks.** Which bots exist, their schedules, their hand-over rules: the advisor reads them itself. The client is asked only what no system knows (is a 2-hour delay acceptable?) and for approval.
- **It names names.** The export only says `grok-bot-default`. Matching each bot's schedule against the wake-up times turns that into "monitor, $79 a week, 64% idle checks".
- **Recommendations are changes, not principles.** Each one names the bot, the current setting and the new one, the saving and its evidence. All of them come as one message the main bot can execute.
- **Each dollar is counted once.** The schedule change and the hand-over overlap, so the hand-over is priced on the wake-ups that remain; idle checks are not counted again as acknowledgements.
- **Imperfect data still gets an answer**, labeled measured, modeled or judgment, with the remaining gap and the next step stated.
- **It closes the loop**: approval, execution through the main bot, KPIs with a check date, and the fresh-chat reminder once, at the end.
