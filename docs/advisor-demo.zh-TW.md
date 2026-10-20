# 顧問 bot：一次諮詢的完整範例

[English](advisor-demo.en.md) · **繁體中文**

以下是一次和顧問 bot 諮詢的完整過程。所有數字都是這個 repo 的腳本對 [`samples/`](../samples/) 合成資料的實際輸出，你可以自己重現：

```bash
bto analyze samples/sample_usage.csv --platform grok-bot --lang zh-TW
bto org samples/demo/org.txt --platform grok-bot --lang zh-TW
bto compare samples/demo/org.txt samples/demo/org_after.txt --platform grok-bot --lang zh-TW
```

---

**使用者：** 這個月 bot 用量翻倍了。這是上週的 usage CSV。

**顧問**（先跑測試，再執行 `bto analyze`）：

> 模型回推上週花費的誤差是 2.1%（模擬 $26.11，實際 $26.67），所以它的推估可以參考。
>
> | 上下文 | 占比 | 喚醒/天 | 輪數/喚醒 | 目前 C | C 成長/天 | 觀察到的重建 |
> |---|---|---|---|---|---|---|
> | researcher | 32% | 8.1 | 3.0 | 75,278 | 8,000 | 0 |
> | chief | 29% | 6.1 | 2.0 | 133,978 | 15,000 | 0 |
> | support/a | 16% | 6.1 | 1.0 | 97,770 | 6,000 | 0 |
> | writer | 15% | 10.1 | 1.6 | 25,401 | 5,000 | 1 |
> | support/b | 4% | 6.1 | 1.0 | 21,308 | 1,000 | 0 |
> | reviewer | 3% | 4.0 | 1.0 | 20,000 | 0 | 0 |
>
> 有三件事特別明顯。它們都是從呼叫模式推斷的，所以標為「疑似」：
>
> - **researcher**：每次喚醒的第一輪幾乎沒有輸出，之後才開始工作，疑似每次被通知後先去讀 board。
> - **writer**：36% 的喚醒只呼叫一輪、輸出 ≤ 30 token，看起來是「收到」之類的回覆。
> - **chief**：上下文約 134K，每天成長 15K，約 4.4 天後會越過 200K 計價門檻。
>
> `support` 同時有兩個 chat，所以我把它當成兩份上下文看：`support/a` 已經 98K 了。
>
> 為了模擬整個團隊，請一次回答以下問題：
> 1. 誰把工作交給誰？大約一天幾次？訊息通常多長？
> 2. 有群組嗎？成員是誰？裡面通常傳什麼？
> 3. bot 收到通知後是直接開工，還是先讀 board 或共享檔案？

**使用者：** chief → researcher 一天約 6 次，簡短的任務說明。researcher → writer 一天 6 次，完整草稿。writer → reviewer 一天 4 次，也是草稿；reviewer → chief 一天 4 次，簡短的結論。writer 會回 researcher「收到」。四個 bot 都在同一個群組，會貼好幾段的進度更新。researcher 每次都會先讀 board。

**顧問**（根據 CSV 實測值和回答，寫出 [`samples/demo/org.txt`](../samples/demo/org.txt)，再執行 `bto org`）：

> **結論：** researcher 占模擬週成本 $157.58 的 73%，主要原因是每次被通知，它都要把一份快速成長的上下文完整讀兩次。
>
> **最該先做的 3 件事**
> 1. **停止 researcher 的「讀 board」來回。** 任務說明直接放在訊息裡。預估省約 1,440 萬 token 等值（來自 `bto org` 的 `board_tool_read`）。
> 2. **禁止「收到」回覆。** writer 的確認訊息每天白白叫醒 researcher 4 次。預估省約 1,330 萬 token 等值。
> 3. **讓 researcher、chief、support/a 交接給新的 chat。** 依目前成長速度，researcher 這週內就會越過 200K，建議約每 2.6 天交接一次；chief 已經超過 120K 的品質軟上限。
>
> 另外：群組平均每則 700 token，每則都會進到四個 bot 的上下文。改成只貼一行狀態。
>
> **假設與限制：** 假設每輪呼叫都重讀完整上下文、上下文成長是線性的、喚醒以 120 秒間隔切分。各項省量會互相重疊，所以我比較的是整個改組方案，而不是把它們加總。

（寫出 [`samples/demo/org_after.txt`](../samples/demo/org_after.txt)，執行 `bto compare`）

> 改組前：$157.58 · 改組後：$21.69 · **0.14 倍**
>
> **下一步：** 套用這三項改善，一週後再匯出一次 CSV，我來確認實際數字是否照模型預測的方向變化。

---

## 顧問 bot 和一般聊天機器人的差別

- **不心算。** 所有數字都來自 `bto`；腳本算不出來的，它會直接說算不出來。
- **一次問完。** 需要的資訊一次問清楚，避免諮詢本身又變成昂貴的來回對話。
- **標明啟發式判斷。** 「疑似讀 board」「疑似確認訊息」是從呼叫模式推斷的，需要對照實際對話確認。
- **自己不常駐。** 諮詢結束時會建議下次開新 chat，讓它自己的上下文也保持精簡。
