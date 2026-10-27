# Step by step: have an existing bot create the Token Advisor

**English** · [繁體中文](SETUP.zh-TW.md)

There are three ways to create the Token Advisor: from the shared template, by asking your main bot, or by hand from the repo (all three are in the [README quick start](../../README.md#quick-start)). This guide covers the second, the usual way on Grok Bot, with every message written out, ready to copy and paste. It takes about 10 minutes. The checks in step 2 and the acceptance test in step 3 apply whichever way you chose. Once set up, the advisor looks up your bots' settings itself, so all you tell it is your goal.

| Step | Talk to | What happens |
|---|---|---|
| 1 | An existing bot (the "main bot") | Create the Token Advisor: workspace, instructions, permissions |
| 2 | Main bot | Check that the instructions are complete and unedited |
| 3 | Token Advisor | Acceptance test with sample data |
| 4 | Token Advisor | First real use: upload your usage export |
| 5 | Both | Everyday use and updates |

> **Before you start:** open a **new chat** with the main bot. Setting up the advisor is a long conversation; held in the main bot's usual long chat, it would be re-read on every future wake-up. That is exactly the cost this project helps you cut.

---

## Step 1: ask the main bot to create the Token Advisor

Paste this to the main bot:

```text
Please create a new bot with exactly this specification. Do not add, drop or "improve" anything:

1. Name: Token Advisor
2. Purpose: only when I message it directly, analyze my bot team's token spend and give advice.
3. Workspace: clone https://github.com/EndeavorYen/bot-token-optimizer into its workspace and keep the folder name bot-token-optimizer. It must be able to run python3 in that folder.
4. Instructions: set the FULL text of adapters/grok-bot/ADVISOR_BOT.en.md from that repo as its instructions, verbatim. Do not summarize, rewrite, translate or drop sections, and do not add rules of your own.
5. Wake-ups: do not add it to any group, do not give it schedules or automations, do not subscribe it to notifications. It wakes up only when I message it directly.
6. Permissions: it needs to READ every bot's settings (name, instructions, schedules and automations, groups, chat length), so it can investigate by itself instead of asking me. It does not need, and must not have, permission to change other bots.

When done, report only these five things, nothing else:
(a) the bot's name
(b) the first line of its instructions
(c) the last heading starting with "## " in its instructions
(d) whether the clone worked, and the output of python3 core/cli.py --version run in its workspace
(e) whether it can read the other bots' settings (yes / no)
```

**Why it is worded this way:**
- **Verbatim:** bots like to "tidy up" long instructions into bullet points. The advisor's method, number rules and examples live in the details; summarized, it drifts back to answering only what was asked.
- **Read, not change:** the advisor has to look up each bot's schedule, instructions and groups itself to give you "change X from every 30 minutes to every 2 hours" instead of asking you. It only proposes; the main bot makes changes after you approve.
- **No groups, no schedules:** every group message wakes each member and makes it re-read its whole context. The advisor should cost nothing while idle.
- **Report only five things:** keeps the main bot's conversation short.

## Step 2: check the main bot's report

A correct report looks like this:

| Item | Should be |
|---|---|
| (a) Name | Token Advisor |
| (b) First line | `# Token Cost Advisor (Grok Bot) — playbook` |
| (c) Last heading | `## Ground rules` |
| (d) Version | `bto` and the same version as the badge at the top of the [README](../../README.md) |
| (e) Reads other bots | yes |

**If something does not match, paste this to the main bot:**

- The first line or the last heading is wrong (the instructions were summarized or cut off):

  ```text
  The Token Advisor's instructions are not the full text. Please read adapters/grok-bot/ADVISOR_BOT.en.md from the repo again and set the whole file, unchanged, as its instructions, replacing what is there now. Then report the first line and the last "## " heading.
  ```

- (e) is "no" (the platform does not allow reading other bots): have the main bot write an inventory into the advisor's workspace instead. Do it once now:

  ```text
  Please write the following to inventory.md in the Token Advisor's workspace, and do not change anything: for every bot, its name; the full text of its instructions; every schedule or automation (interval or times, active hours, time zone); the groups it is in; which bots it notifies and roughly how often; its current chat length if you can see it. Reply only "done" when finished.
  ```

  Paste the same message again whenever your bots change.

- The clone failed (the main bot cannot reach GitHub): on the GitHub page click **Code → Download ZIP**, upload the zip to the main bot and say:

  ```text
  This is the bot-token-optimizer zip. Please unpack it into the Token Advisor's workspace (folder name bot-token-optimizer), and use the full text of adapters/grok-bot/ADVISOR_BOT.en.md from the zip as its instructions, unchanged.
  ```

- `--version` failed (the advisor cannot run code):

  ```text
  The Token Advisor must be able to run python3 in its workspace (standard library only, nothing to install). Please enable code execution for it, then run python3 core/cli.py --version again and report the result.
  ```

## Step 3: acceptance test with sample data

**Open a new chat with the Token Advisor** and paste:

```text
Let's do a trial run. In your workspace, run python3 samples/make_grok_export.py to create a sample usage export and treat it as my data; the bots' schedules are in samples/grok_schedules.txt, as if you had read them from each bot's settings. My time zone is +08:00. My quota currently runs out in about 3 days and I need it to last 7. What do you recommend?
```

**A good answer looks like this** (the sample data is fixed, so the numbers are always the same):

- The first paragraph is the bottom line: about **$158 a week**, and lasting 7 days means getting down to about **$68**; **half of all spend is one bot, monitor** (woken every 30 minutes, re-reading about 110K tokens each time, with **64%** of its checks finding nothing to do).
- The first recommendation is a concrete change: **monitor from every 30 minutes to every 2 hours**, saving about **$41–59 a week**.
- A short table of the top changes with dollars and confidence, about **$72–119 a week** together, so the quota lasts about **5.5–12 days**, plus where the rest of the gap is and how it will look into it.
- Hand-overs are not dropped: each bot gets its own threshold (monitor and digest at 40K tokens, the rest at 100K), about once a day or less.
- A **change request ready to paste to the main bot**, and a question whether to send it.
- At most **one** question, about something only you can decide (e.g. "is noticing an incident up to 2 hours later acceptable?").

**Any of these means it is not following its instructions:**

| Red flag | What it means |
|---|---|
| It asks you "which bots are there", "which ones wake most", "what are the schedules" | It hands back what it could look up |
| Advice like "find the most-woken bot" or "consider fewer wake-ups" | A principle instead of a concrete change |
| One action split in two (e.g. "stay under 200K" and "hand over at 60K") | Double counting |
| Two figures that disagree (e.g. "lasts 4.3 days" and "4–6 days"), or a lever dropped because one setting is impractical | Mental math instead of the tools |
| "Tests passed 78/78", file paths, commands, PASS/FAIL | It is narrating its internal work |
| "Would you like A, B or C?" | A menu instead of a recommendation |
| Every message ends with "open a new chat" | Repeated boilerplate |

If you see a red flag, first tell the Token Advisor:

```text
Please re-read your instructions, especially "The two rules that make you a consultant" and "Fieldwork", and answer my last question again in that way.
```

If nothing changes, the instructions were probably rewritten or are an old version: go back to step 2 and have the main bot set the full text again.

## Step 4: first real use

1. Export the **usage events** CSV from Grok Bot's usage page (the columns start with `Date, Cloud Agent ID, Automation ID, …`; the menu names may differ slightly). The **last 7 days** works best; with less than 2 days, trends are unreliable. When you export, note the quota percentage shown on the usage page and the reset time, and include both with the file.
2. Open a **new chat** with the Token Advisor, attach the file and paste this (fill in the blanks, delete what you don't know):

   ```text
   Attached is the usage export for the last 7 days. My time zone is +08:00.
   Goal: stay under $____ a week (or: make the quota last 7 days).
   Can't change: ____ (e.g. the main bot must stay, replies must not get slower).
   Please give me the bottom line and the one-page memo.
   ```

3. It first looks up each bot's schedules, instructions and groups by itself, then gives you the bottom line, concrete changes and the one-page memo. Its questions are only about goals, constraints or trade-offs (e.g. "is noticing an incident up to 2 hours later acceptable?").
4. If the change request looks right, reply "send it" and it hands it to the main bot. You can also pick only some items, or paste it to the main bot yourself.

## Step 5: everyday use

Talk to the Token Advisor, with a new chat for each engagement.

**Reviewing the organization:**

```text
Can the organization be leaner? Tell me who is redundant, which bots should be merged or split, and show me the best org chart. These must stay as they are: ____ (optional).
```

It reads who notifies whom itself, builds the team model from the measured contexts, and answers with the redundant bots, the merges and splits with their savings, the current and proposed org charts, and a change request.

**A theory question:**

```text
My main bot's context is about ____ tokens and it wakes about __ times a day. Is it worth handing over to a fresh chat now?
```

**Checking a week after the changes** (export the same length of period as last time):

```text
Attached is the usage export for the 7 days since the changes. Please compare it like for like with the last memo: did we hit the KPIs, and what is next?
```

**When the repo has a new version:**

- Update the tools; tell the **Token Advisor**:

  ```text
  Please run git pull on bot-token-optimizer in your workspace and report the output of python3 core/cli.py --version.
  ```

- If the update changed `ADVISOR_BOT.en.md`, also tell the **main bot**:

  ```text
  Please update the Token Advisor's instructions to the latest full text of bot-token-optimizer/adapters/grok-bot/ADVISOR_BOT.en.md in its workspace, unchanged, replacing the old text. Then report the first line and the last "## " heading.
  ```

---

## FAQ

**The Token Advisor seems to wake up often and spend tokens by itself.**
Ask the main bot to list the advisor's groups, schedules and automations, and remove them all. It should wake only when you message it.

**Can I add the advisor to the team's group so it keeps an eye on things?**
Better not. Every group message makes it re-read its whole context, so you would be paying a consultant to sit in on every meeting. Hand it an export when you need it: same result, close to zero cost.

**It says "Automation ID is empty, grouping by model". What does that mean?**
The Grok Bot export usually cannot tell which bot spent the money, only which model was used (e.g. `grok-bot-default`). The advisor splits that bucket itself: scheduled bots wake at known times, so it matches each bot's schedule against the wake-up times in the export. What is left is your own messages and bot-to-bot pings, which it maps from who notifies whom.

**Are the dollars a real bill?**
Not on plan billing (the export shows Included / Free). They are token-priced equivalents, useful for comparing options and as a measure of quota burn.

**Can I use the Chinese version?**
Yes. Use `ADVISOR_BOT.zh-TW.md` in step 1; the first line and last heading in step 2 become `# Token 成本顧問（Grok Bot 版）— 工作手冊` and `## 基本守則`. See [SETUP.zh-TW.md](SETUP.zh-TW.md).
