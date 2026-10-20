# Grok Bot adapter

[English](README.md) · **繁體中文**

在 Grok Bot 建立一個「Token 最佳化顧問」，用量暴增或想調整 bot 分工時呼叫它。

## 平台限制（`--platform grok-bot`）

- 一個群組最多 6 個 Bot，所以控制幅度上限為 6。
- 使用者無法改寫 Bot 的上下文。能用的槓桿是：溝通管道（一對一或群組）、用程式寫檔、減少喚醒、開新 chat 交接。
- usage CSV 由使用者從 Grok Bot 匯出後上傳給顧問 bot。

## 建立顧問 bot

在 Grok Bot 上，新 bot 通常是請既有的 bot 幫你建立。**[SETUP.zh-TW.md](SETUP.zh-TW.md) 有手把手的步驟，每一句要說的話都寫好了：** 要對主 bot 說什麼、怎麼確認指令是全文、有標準答案的驗收測試，以及第一次上傳真實匯出檔。

簡單來說：

1. 在新的 chat 裡請既有的 bot 建立「Token 顧問」：把這個 repo clone 到它的工作區，把 [`ADVISOR_BOT.zh-TW.md`](ADVISOR_BOT.zh-TW.md) 原封不動設為它的指令（英文版為 [`ADVISOR_BOT.en.md`](ADVISOR_BOT.en.md)），給它讀取（唯讀）其他 bot 設定的權限，不要加進群組、排程或自動化。
2. 確認它的指令第一行是 `# Token 成本顧問（Grok Bot 版）— 工作手冊`，最後一節是 `## 基本守則`。
3. 用合成的匯出檔做驗收；標準答案會點名 **monitor** 占每週 $158 的一半，建議把它從每 30 分鐘改成每 2 小時（每週省 $41–59），並附上給主 bot 的變更請求。
4. 之後只在需要時呼叫，每次開新 chat，例如「額度 3 天就用完，我要撐滿 7 天，這是匯出檔」。它會自己查各 bot 的排程、指令和群組，只問你目標和取捨。它會像顧問一樣回答：以每週美元講結論、排好序並標明信心的行動、一頁式備忘錄，以及有檢查日的 KPI（[諮詢範例](../../docs/advisor-demo.zh-TW.md)）。

## 第一次使用真實 CSV

Grok Bot 的 usage-events 匯出檔不用加任何參數就能讀：

```text
Date,Cloud Agent ID,Automation ID,Kind,Model,Max Mode,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,Total Tokens,Cost
```

```bash
python3 core/cli.py report usage-events.csv --platform grok-bot --tz +08:00 --goal-usd-week 300 \
    --schedules schedules.txt --out memo.md --lang zh-TW
```

`schedules.txt` 列出每個 bot 的排程（`monitor every 30m`、`digest at 09:00,18:00`、`sync cron */15 9-18 * * 1-5`，可加 `in=grok-bot-default`）。匯出檔沒有 bot 編號，所以報告會把每個排程和喚醒時間對起來，點名模型分組裡的 bot；剩下的是你自己的訊息和 bot 之間的通知。想先看結果，可以對 `python3 samples/make_grok_export.py` 產生的檔案，搭配 `samples/grok_schedules.txt` 執行同樣的指令（[備忘錄範例](../../docs/sample-memo-grok.zh-TW.md)）。

- prompt 分在三欄，所以 input = `Input (w/ Cache Write)` + `Input (w/o Cache Write)` + `Cache Read`，cached = `Cache Read`（input + output 等於 `Total Tokens`）。
- 有 `Cloud Agent ID` 的資料列是一次性的雲端執行，不是常駐 bot，會排除。
- 方案計費時 `Cost` 是 `Included`、`Free` 這類文字。這時分析器改用價格設定從 token 推算每筆成本，所以校正比的是這個推算值，不是帳單。
- `Automation ID` 當作 bot。它是空的時候（很常見），改依 `Model` 分組（例如 `grok-bot-default`、`grok-bot-automation`）。這種分組裡混了好幾個 bot；`--schedules` 可以把它拆成具名的 bot（見下方）。
- 沒有 chat 編號，所以一組裡可能有好幾個 chat。花費和上下文百分位數仍然正確；成長與校正則是混合值。

其他平台的匯出檔，分析器會自動辨識常見欄名（timestamp、bot_name、input_tokens、cached_input_tokens、output_tokens、reasoning_tokens、cost_usd 等），對不上時用 `--map`：

```bash
python3 core/cli.py analyze usage.csv --platform grok-bot \
  --map time=<時間欄> --map bot=<Bot 欄> --map input=<input token 欄>
```

如果快取 token 不包含在 input 欄位內，加上 `--cached-separate`。確認報告中的「模型校正」誤差在 ±15% 內，再用模型推估改組效果。
