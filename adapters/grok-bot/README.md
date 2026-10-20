# Grok Bot adapter

在 Grok Bot 建立一個「Token 最佳化顧問」，用量暴增或想調整 bot 分工時呼叫它。

## 平台限制（platform.json）

- 一個群組最多 6 個 Bot，所以控制幅度上限為 6。
- 使用者無法改寫 Bot 的上下文。能用的槓桿是：溝通管道（一對一或群組）、用程式寫檔、減少喚醒、開新 chat 交接。
- usage CSV 由使用者從 Grok Bot 匯出後上傳給顧問 bot。

## 建立顧問 bot

1. 在 Grok Bot 新增一個 Bot，例如「Token 顧問」。
2. 把 `ADVISOR_BOT.md` 的內容設為它的指令。
3. 把整個 repo 放進它可存取的工作區（例如請它 clone 這個 repo）。
4. 第一次使用時請它執行 `python3 -m unittest discover -s tests`，確認全部通過。
5. 之後只在需要時呼叫，例如「這週用量暴增，幫我看這份 CSV」或「我想把 leader 拿掉，幫我算算看」。每次諮詢建議開新 chat。

## 第一次使用真實 CSV

分析器會自動辨識常見欄名（timestamp、bot_name、input_tokens、cached_input_tokens、output_tokens、reasoning_tokens、cost_usd 等）。Grok Bot 匯出檔的實際欄名尚未驗證，對不上時用 `--map`：

```bash
python3 core/analyze_usage.py usage.csv --platform adapters/grok-bot/platform.json \
  --map time=<時間欄> --map bot=<Bot 欄> --map input=<input token 欄>
```

如果快取 token 不包含在 input 欄位內，加上 `--cached-separate`。確認報告中的「模型校正」誤差在 ±15% 內，再用模型推估改組效果。
