# Grok Bot adapter

**English** · [繁體中文](README.zh-TW.md)

Set up a "Token Advisor" inside Grok Bot and call it when usage spikes or before you change how your bots divide work.

## Platform limits (platform.json)

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

The analyzer recognizes common column names (timestamp, bot_name, input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, cost_usd, …). The column names in Grok Bot's export have not been verified yet; if they do not match, use `--map`:

```bash
python3 core/analyze_usage.py usage.csv --platform adapters/grok-bot/platform.json \
  --map time=<time column> --map bot=<bot column> --map input=<input-token column>
```

If cached tokens are not included in the input column, add `--cached-separate`. Make sure the "model calibration" error in the report is within ±15% before using the model to predict the effect of a redesign.
