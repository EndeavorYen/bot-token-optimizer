# Step by step: have an existing bot create the Token Advisor

**English** · [繁體中文](SETUP.zh-TW.md)

On Grok Bot, a new bot is usually created by asking one of your existing bots. This guide has every message written out, ready to copy and paste. It takes about 10 minutes.

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
6. Permissions: it does not need, and must not have, permission to change other bots.

When done, report only these four things, nothing else:
(a) the bot's name
(b) the first line of its instructions
(c) the last heading starting with "## " in its instructions
(d) whether the clone worked, and the output of python3 core/cli.py --version run in its workspace
```

**Why it is worded this way:**
- **Verbatim:** bots like to "tidy up" long instructions into bullet points. The advisor's tone, number rules and examples live in the details; summarized, it drifts back to answering only what was asked.
- **No groups, no schedules:** every group message wakes each member and makes it re-read its whole context. The advisor should cost nothing while idle.
- **Report only four things:** keeps the main bot's conversation short.

## Step 2: check the main bot's report

A correct report looks like this:

| Item | Should be |
|---|---|
| (a) Name | Token Advisor |
| (b) First line | `# Token Cost Advisor (Grok Bot) — playbook` |
| (c) Last heading | `## Ground rules` |
| (d) Version | `bto 0.3.0` (or newer) |

**If something does not match, paste this to the main bot:**

- The first line or the last heading is wrong (the instructions were summarized or cut off):

  ```text
  The Token Advisor's instructions are not the full text. Please read adapters/grok-bot/ADVISOR_BOT.en.md from the repo again and set the whole file, unchanged, as its instructions, replacing what is there now. Then report the first line and the last "## " heading.
  ```

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
Let's do a trial run. In your workspace, run python3 samples/make_grok_export.py to create a sample usage export and treat it as my data. My time zone is +08:00 and I would like to stay under $60 a week. What do you think?
```

**A good answer looks like this** (the sample data is fixed, so the numbers are always the same):

- The first paragraph is the bottom line: about **$80 a week**, **96%** of it in `grok-bot-default` (and it says that bucket mixes several bots), almost all of it spent re-reading context.
- The actions together save about **$28–57 a week**, enough for the $60 target, with overall confidence stated as **low** and why.
- The biggest action is "hand over to a fresh chat once a context passes 60,000 tokens".
- A short table of the top actions, each with dollars and confidence.
- At most **one** question for you.

**Any of these means it is not following its instructions:**

| Red flag | What it means |
|---|---|
| "Tests passed 78/78", file paths, commands, PASS/FAIL | It is narrating its internal work |
| "Would you like A, B or C?" | A menu instead of a recommendation |
| "Calibration failed, so I cannot estimate" | It stops when the data is imperfect |
| Every message ends with "open a new chat" | Repeated boilerplate |
| No mention of the 96% bucket | It missed the biggest driver |

If you see a red flag, first tell the Token Advisor:

```text
Please re-read your instructions, especially "How you sound" and "How you use numbers", and answer my last question again in that way.
```

If nothing changes, the instructions were probably rewritten: go back to step 2 and have the main bot set the full text again.

## Step 4: first real use

1. Export the **usage events** CSV from Grok Bot's usage page (the columns start with `Date, Cloud Agent ID, Automation ID, …`; the menu names may differ slightly). The **last 7 days** works best; with less than 2 days, trends are unreliable.
2. Open a **new chat** with the Token Advisor, attach the file and paste this (fill in the blanks, delete what you don't know):

   ```text
   Attached is the usage export for the last 7 days. My time zone is +08:00.
   Goal: stay under $____ a week (or: stop hitting the quota).
   Can't change: ____ (e.g. the main bot must stay, replies must not get slower).
   Please give me the bottom line and the one-page memo.
   ```

3. Read the bottom line and the memo. The one question it asks usually decides the next step (for example "which bots sit inside the default bucket?"); just answer it.
4. Choose which actions to take, fill in the "Owner" column of the memo, and work through them in the "this week / next week" order.

## Step 5: everyday use

Talk to the Token Advisor, with a new chat for each engagement.

**Reorganizing:**

```text
I want to change how the bots divide work: ____ (e.g. remove the leader and let workers report to the main bot).
Today: ____ → ____ about __ times a day, messages about ____ (a line / a paragraph / a document); groups: ____.
Compare before and after and tell me whether it is worth it.
```

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
The Grok Bot export usually cannot tell which bot spent the money, only which model was used (e.g. `grok-bot-default`). The dollars are right, but one bucket mixes several bots. For a per-bot split, export each bot separately, or run recurring work as named automations.

**Are the dollars a real bill?**
Not on plan billing (the export shows Included / Free). They are token-priced equivalents, useful for comparing options and as a measure of quota burn.

**Can I use the Chinese version?**
Yes. Use `ADVISOR_BOT.zh-TW.md` in step 1; the first line and last heading in step 2 become `# Token 成本顧問（Grok Bot 版）— 工作手冊` and `## 基本守則`. See [SETUP.zh-TW.md](SETUP.zh-TW.md).
