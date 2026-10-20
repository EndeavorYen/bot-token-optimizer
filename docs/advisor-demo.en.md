# Advisor bot: a sample engagement

**English** · [繁體中文](advisor-demo.zh-TW.md)

This is what working with the advisor bot looks like, end to end. The advisor follows the consultant playbook in [`ADVISOR_BOT.en.md`](../adapters/grok-bot/ADVISOR_BOT.en.md): frame the goal, diagnose, recommend, plan, verify. Every number below is real output from this repo's tools, run on the synthetic data in [`samples/`](../samples/). Reproduce it:

```bash
bto report samples/sample_usage.csv --platform grok-bot --goal-usd-week 18 --out memo.md
bto org samples/demo/org.txt --platform grok-bot
bto compare samples/demo/org.txt samples/demo/org_after.txt --platform grok-bot
```

---

**Client:** Our bot usage doubled this month. Here's last week's export. We need to get it under about $18 a week.

**Advisor** *(runs `bto report` with the $18 target; the self-check and the commands stay out of the answer)*:

> Your bots run at about **$27 a week** at the current pace, so reaching $18 means cutting about a third. Almost all of it (96%) is bots re-reading their own chats, not writing. Three of your contexts carry 77% of the cost, and two of them are growing fast.
>
> | # | Action | Saves / week | How sure |
> |---|---|---|---|
> | 1 | Hand over to a fresh chat once a chat passes 60K tokens (chief and support/a today) | $3.40–6.70 | modeled, medium |
> | 2 | Put the brief in the ping instead of "go read the board" (researcher) | $1.90–3.80 | measured, medium |
> | 3 | Stop breaking the prompt cache mid-wake-up | $1.30–2.60 | measured, medium |
> | 4 | No more "OK" replies (writer) | $0.50–0.90 | measured, medium |
>
> Together that is **$7–14 a week**, counting each dollar once. Reaching $18 takes about **$2 a week more**, which means waking researcher about 23% less often or accepting a slightly higher budget. That is your call. The one-page memo is attached ([example](sample-memo.en.md)): where the money goes, these actions with owners to fill in, and the five numbers we will check on 14 October.
>
> One question so I can also check the team structure: who hands work to whom, about how often, and is there a group chat everyone posts in?

**Client:** chief → researcher about 6 a day, short briefs. researcher → writer 6 a day, full drafts. writer → reviewer 4 a day; reviewer → chief 4 a day, short verdicts. writer replies "got it" to researcher. All four are in a group where they post progress updates, a few paragraphs each. researcher always reads the board first.

**Advisor** *(writes [`samples/demo/org.txt`](../samples/demo/org.txt) from the measurements and the answers, runs `bto org`, then tests a redesign with `bto compare`)*:

> First, a reconciliation. The team as you describe it would cost about **$158 a week**, far more than the $27 in the export. Either the export covers only part of the traffic (one workspace, or the busy days are missing), or the group posts are shorter than "a few paragraphs". Before you act on the dollar figures below, please check that the export covers every bot. The **ratio** holds either way.
>
> As described, the structure has three costly habits:
> - every group post lands in all four contexts (about 700 tokens each);
> - researcher pays twice per ping: once to read the board and again to work;
> - "got it" replies wake researcher four times a day for nothing.
>
> I tested the redesign (no acknowledgements, briefs in the message, one-line status in the group, hand-overs at a set threshold):
>
> | | Before | After |
> |---|---|---|
> | Modeled cost / week | $157.58 | $21.69 (**0.14×**) |
> | Most expensive bot | researcher, 73% | (spread out) |
>
> **Plan.** This week: the two instruction changes (no "OK", briefs in the ping); they cost nothing and need no restructuring. Next week: hand-overs at 60K and one-line group status. **Decision for you:** whether researcher's wake-up frequency is a business need. **Data:** an export that covers all bots.
>
> **Check on 14 October:** weekly spend ≤ $18, "OK" wake-ups ≤ 5%, board-first wake-ups ≤ 10%, cache share ≥ 80%, researcher's typical context ≤ 45K. Export the same seven days again and I will compare like for like. Next time, start a fresh chat with me; it keeps my own costs down.

---

## What makes this a consultant and not a calculator

- **Answer first, in dollars per week**, then the few numbers that support a decision. Method, column mappings and self-checks stay out of the answer; they are in the memo's appendix.
- **It frames the goal.** It restates the target, works out the gap ("a third") and says what reaching it takes, including the trade-off the client has to decide.
- **Imperfect data still gets an answer.** Every figure carries its evidence tier (measured, modeled or judgment) and a confidence level. When something is unknown, the advisor says how to find out.
- **It is sceptical of its own inputs.** The described team and the export disagree, so it reconciles them before recommending a redesign.
- **It closes the loop.** Owners, a sequence, the decisions only the client can make, and KPIs with a check date.
- **It spends little itself.** One round of questions, no acknowledgements, and the fresh-chat reminder once, at the end.
