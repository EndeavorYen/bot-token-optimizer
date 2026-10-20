# Grok Bot adapter

**English** · [繁體中文](README.zh-TW.md)

Set up a "Token Advisor" inside Grok Bot and call it when usage spikes or before you change how your bots divide work.

## Platform limits (`--platform grok-bot`)

- A group holds at most 6 bots, so the span limit is 6.
- Users cannot rewrite a bot's context. The levers available are: communication channel (one-to-one or group), writing data to files with code, fewer wake-ups, and handing over to a fresh chat.
- The usage CSV is exported from Grok Bot by the user and uploaded to the advisor bot.

## Create the advisor bot

1. Add a new Bot in Grok Bot, e.g. "Token Advisor".
2. Set the contents of [`ADVISOR_BOT.en.md`](ADVISOR_BOT.en.md) as its instructions ([`ADVISOR_BOT.zh-TW.md`](ADVISOR_BOT.zh-TW.md) for Chinese).
3. Put the whole repo where the bot can access it (e.g. ask it to clone this repo).
4. On first use, ask it to run `python3 -m unittest discover -s tests` and confirm everything passes.
5. Call it only when needed, e.g. "usage exploded this week, look at this CSV" or "I want to remove the leader, run the numbers". Start a fresh chat for each consultation.

## Your first real CSV

The Grok Bot usage-events export is read without any flags:

```text
Date,Cloud Agent ID,Automation ID,Kind,Model,Max Mode,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,Total Tokens,Cost
```

- The prompt is split over three columns, so input = `Input (w/ Cache Write)` + `Input (w/o Cache Write)` + `Cache Read`, and cached = `Cache Read` (input + output equals `Total Tokens`).
- Rows with a `Cloud Agent ID` are one-shot cloud runs, not persistent bots, and are left out.
- On plan billing `Cost` holds text such as `Included` or `Free`. The analyzer then prices every call from tokens with the pricing preset, so the calibration compares the model with that estimate, not with a bill.
- The export has no bot or chat column, so all calls are analyzed as one context. `Model` (e.g. `grok-bot-default`, `grok-bot-automation`) is the model, not the bot; add a bot column yourself before analyzing if you need a per-bot breakdown, and treat the calibration error of a single merged context with care.

For other exports the analyzer recognizes common column names (timestamp, bot_name, input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, cost_usd, …); if they do not match, use `--map`:

```bash
python3 core/cli.py analyze usage.csv --platform grok-bot \
  --map time=<time column> --map bot=<bot column> --map input=<input-token column>
```

If cached tokens are not included in the input column, add `--cached-separate`. Make sure the "model calibration" error in the report is within ±15% before using the model to predict the effect of a redesign.
