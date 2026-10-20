# Grok Bot adapter

[English](README.md) · **繁體中文**

在 Grok Bot 建立一個「Token 最佳化顧問」，用量暴增或想調整 bot 分工時呼叫它。

## 平台限制（`--platform grok-bot`）

- 一個群組最多 6 個 Bot，所以控制幅度上限為 6。
- 使用者無法改寫 Bot 的上下文。能用的槓桿是：溝通管道（一對一或群組）、用程式寫檔、減少喚醒、開新 chat 交接。
- usage CSV 由使用者從 Grok Bot 匯出後上傳給顧問 bot。

## 建立顧問 bot

1. 在 Grok Bot 新增一個 Bot，例如「Token 顧問」。
2. 把 [`ADVISOR_BOT.zh-TW.md`](ADVISOR_BOT.zh-TW.md) 的內容設為它的指令（英文版為 [`ADVISOR_BOT.en.md`](ADVISOR_BOT.en.md)）。
3. 把整個 repo 放進它可存取的工作區（例如請它 clone 這個 repo）。
4. 第一次使用時請它執行 `python3 -m unittest discover -s tests`，確認全部通過。
5. 之後只在需要時呼叫，例如「這週用量暴增，幫我看這份 CSV」或「我想把 leader 拿掉，幫我算算看」。每次諮詢建議開新 chat。

## 第一次使用真實 CSV

Grok Bot 的 usage-events 匯出檔不用加任何參數就能讀：

```text
Date,Cloud Agent ID,Automation ID,Kind,Model,Max Mode,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,Total Tokens,Cost
```

- prompt 分在三欄，所以 input = `Input (w/ Cache Write)` + `Input (w/o Cache Write)` + `Cache Read`，cached = `Cache Read`（input + output 等於 `Total Tokens`）。
- 有 `Cloud Agent ID` 的資料列是一次性的雲端執行，不是常駐 bot，會排除。
- 方案計費時 `Cost` 是 `Included`、`Free` 這類文字。這時分析器改用價格設定從 token 推算每筆成本，所以校正比的是這個推算值，不是帳單。
- 匯出檔沒有 bot 或 chat 欄位，所有呼叫會合併成一份上下文分析。`Model`（例如 `grok-bot-default`、`grok-bot-automation`）是模型，不是 bot；需要分 bot 時請先自己補一個 bot 欄位，合併成一份上下文時的校正誤差也要保守看待。

其他平台的匯出檔，分析器會自動辨識常見欄名（timestamp、bot_name、input_tokens、cached_input_tokens、output_tokens、reasoning_tokens、cost_usd 等），對不上時用 `--map`：

```bash
python3 core/cli.py analyze usage.csv --platform grok-bot \
  --map time=<時間欄> --map bot=<Bot 欄> --map input=<input token 欄>
```

如果快取 token 不包含在 input 欄位內，加上 `--cached-separate`。確認報告中的「模型校正」誤差在 ±15% 內，再用模型推估改組效果。
