# bot-token-optimizer

**Your bots are burning tokens on wake-ups, relays and bloated context.** Find where the tokens go from your usage CSV, and fix how your bots talk to each other.

**你的 bot 把 token 浪費在重複喚醒、轉話和越來越大的上下文。** 從 usage CSV 找出消耗來源，改掉 bot 之間的溝通方式。

---

## Why bots are different

A persistent bot re-reads its **entire context on every wake-up**. So cost is roughly:

```
cost ≈ wake-ups × calls per wake-up × context size (C)
```

Shorter messages barely help. What helps is fewer wake-ups and a smaller C. That turns "how should my bots be organized" into a cost problem you can compute:

| Common habit | What it actually costs |
| --- | --- |
| Bots replying "OK" / "got it" | Each one makes the receiver re-read its full context |
| A leader that only forwards messages | ~1.9× the cost of a direct hand-off |
| Notify, then the receiver reads a shared board via a tool | Up to 2× tokens per notification |
| Never starting a fresh chat | Every wake-up gets pricier; past 200K the unit price doubles (Grok) |

A summarizing leader, by contrast, can be *cheaper* than a flat team (0.9×), and rebuilding a bloated bot usually pays for itself within 2–3 wake-ups.

## What's inside

```
docs/guide.md                 The full guide (theory + practice, 中文)
core/orgopt.py                Cost model: board / scenario / span / reset / org / compare / cost
core/analyze_usage.py         Usage CSV analyzer → per-bot wake-ups, context growth, waste patterns
pricing/grok-4.7.json         Price presets
adapters/grok-bot/            Grok Bot: platform limits + advisor bot instructions
samples/                      Example orgs (before / after) and a synthetic usage CSV
tests/                        32 tests (hand-calculated values, planted-pattern detection, calibration)
```

Pure Python standard library. No dependencies.

## Quick start

```bash
# 1. Where are my tokens going?
python3 core/analyze_usage.py usage.csv --platform adapters/grok-bot/platform.json --org-out org.json

# 2. Diagnose an organization (fill in who talks to whom in org.json first)
python3 core/orgopt.py --platform adapters/grok-bot/platform.json org org.json

# 3. Compare a redesign
python3 core/orgopt.py compare samples/org_before.json samples/org_after.json

# Theory
python3 core/orgopt.py board --k 1                      # is "write to board, then ping" worth it?
python3 core/orgopt.py span --n 12 --c0 30000 --m 1000  # how many bots per manager?
python3 core/orgopt.py reset --c 150000 --c-base 8000   # time to start a fresh chat?

# Verify
python3 -m unittest discover -s tests
```

The sample redesign (remove a relay leader, ban acks, stop board round-trips, slim the group chat, reset bloated contexts) drops the modeled weekly cost to about **0.1×**.

## Advisor bot

`adapters/grok-bot/ADVISOR_BOT.md` turns this repo into an on-demand advisor bot: upload a usage CSV when costs spike, or describe a reorg, and it answers with numbers produced only by the tested scripts. See [adapters/grok-bot/README.md](adapters/grok-bot/README.md).

## Other bot platforms

The model is platform-agnostic. To add a platform, create `adapters/<platform>/platform.json`:

```json
{"name": "My Bot Platform", "pricing": "../../pricing/<preset>.json", "span_cap": 8}
```

Add a pricing preset if needed, and adapt the advisor instructions to the platform's limits.

## Limits

The model gives direction and magnitude, not an exact bill. It assumes every call re-reads the full context, approximates context growth as linear, and does not price in quality loss from long contexts or hand-offs. The Grok Bot usage CSV column names have not yet been verified against a real export; the analyzer auto-maps common names and accepts `--map` overrides. Details in [docs/guide.md](docs/guide.md).

---

## 中文說明

bot 是常駐的，每次被喚醒都要重讀完整上下文，所以成本約等於「喚醒次數 × 每次喚醒的呼叫輪數 × 上下文大小」。縮短訊息幫助有限，減少喚醒、縮小上下文才是主要槓桿，而這正取決於 bot 之間怎麼分工、怎麼溝通。

這個專案提供：

- **指引**：[docs/guide.md](docs/guide.md)，包含成本模型、四組公式（board、leader、控制幅度、重建門檻）、架構決策流程、八個反模式與改善做法。
- **工具**：`core/analyze_usage.py` 分析 usage CSV，`core/orgopt.py` 模擬與比較組織方案。
- **顧問 bot**：`adapters/grok-bot/ADVISOR_BOT.md`，用量暴增或想改組時呼叫，所有數字都由測試過的腳本計算。

目前以 Grok Bot 為第一個落地平台，模型本身不綁平台，其他 bot 平台可新增 adapter 使用。
