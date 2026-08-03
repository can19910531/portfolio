# 📈 Stock Morning Brief — AI 股票晨報機器人

每天早上自動做完這些事，推到你的 Telegram：

- 🔥 **市場重點**：AI 網搜 24 小時內新聞，濃縮 5~8 條（附來源連結）
- 🩺 **自選股健檢**：短／中／長期趨勢箭頭 ▲▼——由 15 個技術訊號投票算出，不是 AI 的感覺
- 📊 **K線＋MACD 圖**：大盤＋每檔自選股，紅漲綠跌台股配色

> An AI-powered daily stock briefing bot: market news digest + rule-based
> trend health-check + candlestick/MACD charts, delivered to Telegram.
> **Setup is an AI interview** — clone it, then tell your AI assistant to read
> [`docs/SETUP_AGENT.md`](docs/SETUP_AGENT.md) and it walks you through everything.

## ✨ 這個專案特別的地方：AI 訪談式安裝

不用改程式碼、不用讀文件。Clone 之後，對你的 AI（Claude Code、Cursor⋯⋯）說一句：

```
讀 docs/初始設定任務書.md，幫我完成設定
```

AI 會逐題問你：

1. 看**台股**、**美股**還是都要？
2. 想追蹤哪幾檔**自選股**？
3. 要哪些**技術指標**？（K線+MACD／加均線）
4. 每天**幾點**推播？
5. 報告存**本機**還是雲端？（預設本機，零依賴）
6. 然後帶你 3 分鐘建好 Telegram bot

答完它就把 `config.yaml` 寫好、發測試訊息、建好每日排程。

## 🚀 快速開始

```bash
git clone https://github.com/can19910531/portfolio.git
cd portfolio/stock-morning-brief
pip install -r requirements.txt
# 然後叫你的 AI 讀 docs/初始設定任務書.md（英文版 docs/SETUP_AGENT.md）
```

沒有 AI 也能手動裝：複製 `config.example.yaml` → `config.yaml`、`.env.example` → `.env`，填一填，排程跑 `charts.py` ＋ `notify.py`。

## 🧱 架構

```
每天 08:00（排程觸發）
   │
   ├─ charts.py ── Yahoo Finance 抓 2 年日線
   │      ├→ K線＋MACD 圖（大盤＋自選股）
   │      └→ 健檢表（短/中/長期箭頭，15 個技術訊號投票）
   │
   ├─ AI agent（可選）── 網搜市場新聞 → 依模板寫晨報
   │      └ docs/每日晨報任務書.md 就是 AI 的 SOP
   │
   └─ notify.py ── Telegram 推播（文字先、圖後）
          └ IPv6 優先連線、自動重試、失敗寫 log、補推不重複
```

兩種跑法：

| 模式 | 需要什麼 | 拿到什麼 |
|---|---|---|
| **純腳本** | Python＋排程器 | 圖表＋健檢表 |
| **AI 加值** | 會網搜的 AI agent（讀 `docs/每日晨報任務書.md`） | 再加市場新聞重點段 |

## 📁 專案結構

```
├── charts.py                 # 抓行情、畫圖、算健檢箭頭
├── notify.py                 # Telegram 推播（文字/圖片）
├── config.example.yaml       # 設定範例（市場/自選股/指標/時間/儲存）
├── .env.example              # Telegram 機密範例
├── requirements.txt
└── docs/
    ├── 初始設定任務書.md       # AI 安裝訪談腳本（中文）
    ├── SETUP_AGENT.md         # 同上（English）
    └── 每日晨報任務書.md       # AI 每日執行 SOP
```

## 🔒 隱私與安全

- 全部在**你自己的電腦**上跑；行情來自 Yahoo Finance 公開資料，免金鑰
- Telegram token 只存在本機 `.env`（已 gitignore）
- 預設**本機儲存**，雲端備份是可選項
- **不下單、不碰金流、不給投資建議**——只做資訊彙整

## 📌 免責聲明

本工具產出為公開資訊之自動彙整，僅供參考，**非投資建議**。投資有風險，決策請自行判斷。

## License

MIT
