# Grok Bot adapter

**English** · [繁體中文](README.zh-TW.md)

Set up a "Token Advisor" inside Grok Bot and call it when usage spikes or before you change how your bots divide work.

## Platform limits (`--platform grok-bot`)

- A group holds at most 6 bots, so the span limit is 6.
- Users cannot rewrite a bot's context. The levers available are: communication channel (one-to-one or group), writing data to files with code, fewer wake-ups, and handing over to a fresh chat.
- The usage CSV is exported from Grok Bot by the user and uploaded to the advisor bot.

## Create the advisor bot

On Grok Bot you usually ask an existing bot to create a new one. **[SETUP.en.md](SETUP.en.md) walks you through it step by step, with every message ready to paste:** what to tell the main bot, how to check the instructions were copied in full, an acceptance test with known answers, and your first real export.

In short:

1. In a fresh chat, ask an existing bot to create "Token Advisor": clone this repo into its workspace, set [`ADVISOR_BOT.en.md`](ADVISOR_BOT.en.md) verbatim as its instructions ([`ADVISOR_BOT.zh-TW.md`](ADVISOR_BOT.zh-TW.md) for Chinese), give it read-only access to the other bots' settings, and keep it out of groups, schedules and automations.
2. Check that its instructions start with `# Token Cost Advisor (Grok Bot) — playbook` and end with the `## Ground rules` section.
3. Run the acceptance test on the synthetic export; the expected answer names **monitor** as half of the $158/week and recommends running it every 2 hours instead of every 30 minutes ($41–59/week), with a change request ready for the main bot.
4. Call it only when needed, with a new chat each time, e.g. "the quota runs out in 3 days, I need it to last 7; here is the export". It looks up your bots' schedules, instructions and groups itself and asks you only about goals and trade-offs. It answers like a consultant: the bottom line in dollars per week, ranked actions with their confidence, a one-page memo and KPIs with a check date ([sample engagement](../../docs/advisor-demo.en.md)).

## Your first real CSV

The Grok Bot usage-events export is read without any flags:

```text
Date,Cloud Agent ID,Automation ID,Kind,Model,Max Mode,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,Total Tokens,Cost
```

```bash
python3 core/cli.py report usage-events.csv --platform grok-bot --tz +08:00 --goal-usd-week 300 \
    --schedules schedules.txt --out memo.md
```

`schedules.txt` lists each bot's schedule (`monitor every 30m`, `digest at 09:00,18:00`, `sync cron */15 9-18 * * 1-5`, optionally `in=grok-bot-default`). Since the export has no bot id, the report matches each schedule against the wake-up times to name the bots inside a model bucket; what is left is your own messages and bot-to-bot pings. To see the result before exporting your own data, run the same command on `python3 samples/make_grok_export.py` with `samples/grok_schedules.txt` ([sample memo](../../docs/sample-memo-grok.en.md)).

- The prompt is split over three columns, so input = `Input (w/ Cache Write)` + `Input (w/o Cache Write)` + `Cache Read`, and cached = `Cache Read` (input + output equals `Total Tokens`).
- Rows with a `Cloud Agent ID` are one-shot cloud runs, not persistent bots, and are left out.
- On plan billing `Cost` holds text such as `Included` or `Free`. The analyzer then prices every call from tokens with the pricing preset, so the calibration compares the model with that estimate, not with a bill.
- `Automation ID` is used as the bot. When it is empty (common), the call is grouped by `Model` instead (e.g. `grok-bot-default`, `grok-bot-automation`). Such a bucket mixes several bots; `--schedules` splits it into named bots (below).
- There is no chat id, so several chats can share a bucket. Spend and context percentiles are still right; growth and calibration are blended.

For other exports the analyzer recognizes common column names (timestamp, bot_name, input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, cost_usd, …); if they do not match, use `--map`:

```bash
python3 core/cli.py analyze usage.csv --platform grok-bot \
  --map time=<time column> --map bot=<bot column> --map input=<input-token column>
```

If cached tokens are not included in the input column, add `--cached-separate`. Make sure the "model calibration" error in the report is within ±15% before using the model to predict the effect of a redesign.
