# Contributing / 參與貢獻

**English** · [繁體中文](#中文)

The most valuable contribution right now is **real data**. The model is calibrated on synthetic data, and the usage-CSV column names of most bot platforms have not been verified yet.

- **Share your CSV schema.** Open a [CSV schema issue](.github/ISSUE_TEMPLATE/csv-schema.md) with the column names and two or three anonymized rows from your platform's usage export. That is enough to add auto-mapping for your platform.
- **Report a calibration result.** Run `bto analyze your.csv` and tell us the "model calibration" error. Large errors are especially useful.
- **Add a platform.** Create `core/platforms/<name>.json` (span limit, pricing preset) and, if the platform has a different price list, `core/pricing/<model>.json` with a `source` URL and a `checked` date. Then run `python docs/calculator/build_presets.py`.
- **Code changes.** Keep the standard library only. Add tests (`python -m unittest discover -s tests`). If you change a formula, port it to `docs/calculator/calc.js` too; `node tests/calculator_parity.mjs` must pass. If figures change, run `python docs/images/make_figures.py`; if reports change, run `python docs/make_memos.py` to regenerate the sample memos and redesign.
- **Docs** come in English and 繁體中文; please update both, or say in the PR which one is missing.

Never upload real conversations or anything that identifies people or customers. Token counts and timestamps are enough.

## 中文

目前最有價值的貢獻是**真實資料**。模型目前是用合成資料校正的，大多數 bot 平台的 usage CSV 欄位也還沒驗證過。

- **分享你的 CSV 欄位。** 開一個 [CSV 欄位 issue](.github/ISSUE_TEMPLATE/csv-schema.md)，附上平台 usage 匯出檔的欄位名稱和兩三筆去識別化的資料，我們就能幫你的平台加上自動對應。
- **回報校正結果。** 執行 `bto analyze your.csv`，告訴我們「模型校正」的誤差。誤差大的案例特別有幫助。
- **新增平台。** 建立 `core/platforms/<名稱>.json`（控制幅度上限、價格設定）；如果價格不同，加上 `core/pricing/<模型>.json`，並附上 `source` 網址和 `checked` 日期，然後執行 `python docs/calculator/build_presets.py`。
- **程式修改。** 只使用 Python 標準函式庫，並附上測試（`python -m unittest discover -s tests`）。改了公式，請一併修改 `docs/calculator/calc.js`，確保 `node tests/calculator_parity.mjs` 通過；圖表有變動時，執行 `python docs/images/make_figures.py`；報告內容有變動時，執行 `python docs/make_memos.py` 重新產生範例備忘錄與範例改組。
- **文件**有英文和繁體中文兩個版本，請兩邊一起更新，或在 PR 中註明缺少哪一個。

請勿上傳真實對話內容，或任何能辨識個人、客戶的資訊。token 數量和時間戳記就足夠了。
