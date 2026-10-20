---
name: Share a usage CSV schema / 分享 usage CSV 欄位
about: Help us auto-map your bot platform's usage export / 幫你的 bot 平台加上自動欄位對應
title: "CSV schema: <platform>"
labels: csv-schema
---

**Platform / 平台:** (e.g. Grok Bot)

**Where the export comes from / 匯出位置:** (menu path or API)

**Header row / 欄位名稱列:**

```
paste the header row here
```

**Two or three anonymized rows / 兩三筆去識別化資料:**

```
paste rows here (token counts and timestamps only — no message content)
```

**Notes / 備註:** Are cached tokens included in the input column? Is there a chat/thread id? Is cost included?
快取 token 是否包含在 input 欄位內？有沒有 chat/thread id？有沒有成本欄位？

**Calibration result (optional) / 校正結果（選填）:** output of `bto analyze your.csv` → "Model calibration" line
