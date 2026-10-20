# Advisor bot: a sample consultation

**English** · [繁體中文](advisor-demo.zh-TW.md)

This is what a consultation with the advisor bot looks like, end to end. Every number below is real output from the scripts in this repo, run on the synthetic data in [`samples/`](../samples/). Reproduce it yourself:

```bash
bto analyze samples/sample_usage.csv --platform grok-bot
bto org samples/demo/org.txt --platform grok-bot
bto compare samples/demo/org.txt samples/demo/org_after.txt --platform grok-bot
```

---

**User:** Our bot usage doubled this month. Here is last week's usage CSV.

**Advisor** *(runs the tests, then `bto analyze`)*:

> The model reproduces last week's spend within 2.1% ($26.11 modeled vs $26.67 actual), so its estimates are usable.
>
> | Context | Share | Wake-ups/day | Calls/wake-up | C now | C growth/day | Resets seen |
> |---|---|---|---|---|---|---|
> | researcher | 32% | 8.1 | 3.0 | 75,278 | 8,000 | 0 |
> | chief | 29% | 6.1 | 2.0 | 133,978 | 15,000 | 0 |
> | support/a | 16% | 6.1 | 1.0 | 97,770 | 6,000 | 0 |
> | writer | 15% | 10.1 | 1.6 | 25,401 | 5,000 | 1 |
> | support/b | 4% | 6.1 | 1.0 | 21,308 | 1,000 | 0 |
> | reviewer | 3% | 4.0 | 1.0 | 20,000 | 0 | 0 |
>
> Three things stand out, all marked "likely" because they are inferred from the call pattern:
>
> - **researcher**: every wake-up's first call produces almost nothing before the real work starts. It is likely reading a board after each ping.
> - **writer**: 36% of its wake-ups are a single call with ≤ 30 output tokens. Those look like "OK" replies.
> - **chief**: context is about 134K and grows 15K a day. It will cross the 200K price tier in about 4.4 days.
>
> `support` runs two chats, so I treat them as two contexts: `support/a` is already at 98K.
>
> To model the team I need a few answers, all at once:
> 1. Who hands work to whom, about how often, and how long are those messages?
> 2. Is there a group chat? Who is in it and what gets posted?
> 3. When a bot is pinged, does it start working, or first read a board or shared file?

**User:** chief → researcher about 6 a day, short briefs. researcher → writer 6 a day, full drafts. writer → reviewer 4 a day, drafts again; reviewer → chief 4 a day, short verdicts. writer replies "got it" to researcher. All four are in a group where they post progress updates, a few paragraphs each. researcher always reads the board first.

**Advisor** *(writes [`samples/demo/org.txt`](../samples/demo/org.txt) from the CSV measurements and the answers, then runs `bto org`)*:

> **Conclusion:** researcher is 73% of the modeled weekly cost of $157.58, mostly because every ping costs it two full reads of a fast-growing context.
>
> **Top 3 things to do first**
> 1. **Stop the board round-trips into researcher.** Put the brief in the message itself. Estimated saving: about 14.4M token equivalents (from `bto org`, finding `board_tool_read`).
> 2. **Ban "got it" replies.** writer's acknowledgements wake researcher four times a day for nothing. About 13.3M token equivalents.
> 3. **Hand over researcher, chief and support/a to fresh chats.** researcher crosses 200K within the week at its current growth; suggested hand-over every 2.6 days. chief is already above the 120K quality soft cap.
>
> Also: the group chat averages 700 tokens a message and every message lands in four contexts. Keep it to one-line status updates.
>
> **Assumptions and limits:** every call re-reads the full context; growth is linear; wake-ups are split at 120-second gaps. The savings overlap, so I compared the whole redesign instead of adding them up.

*(writes [`samples/demo/org_after.txt`](../samples/demo/org_after.txt) and runs `bto compare`)*

> Before: $157.58 · After: $21.69 · **0.14×**
>
> **Next step:** apply the three changes, export the CSV again in a week, and I will check whether the real numbers moved the way the model says.

---

## What the advisor does differently from a generic chatbot

- **No mental math.** Every number comes from `bto`; if a script cannot compute something, it says so.
- **One round of questions.** It asks everything it needs at once, so the consultation does not become another expensive back-and-forth.
- **Heuristics are labeled.** "Likely board read" and "likely acknowledgement" are inferred from call patterns and must be checked against real conversations.
- **It does not keep itself running.** At the end it suggests a fresh chat next time, so its own context stays small.
