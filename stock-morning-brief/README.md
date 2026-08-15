# 📈 Stock Morning Brief — AI 股票晨報機器人

每天早上自動做完這些事，推到你的 Telegram：

- 🔥 **市場重點**：AI 網搜 24 小時內新聞，濃縮 5~8 條（附來源連結，**且會被查核**）
- 🩺 **自選股健檢**：短／中／長期趨勢箭頭 ▲▼——由 15 個技術訊號投票算出，不是 AI 的感覺
- 📊 **K線＋MACD 圖**：大盤＋每檔自選股，紅漲綠跌台股配色

> An AI-powered daily stock briefing bot: verified market-news digest +
> rule-based trend health-check + candlestick/MACD charts, delivered to Telegram.
> **Setup is an AI interview** — clone it, then tell your AI assistant to read
> [`docs/SETUP_AGENT.md`](docs/SETUP_AGENT.md) and it walks you through everything.

## ✨ 兩個特別的設計

### 1. AI 訪談式安裝

不用改程式碼、不用讀文件。Clone 之後，對你的 AI（Claude Code、Cursor⋯⋯）說一句：

```
讀 docs/初始設定任務書.md，幫我完成設定
```

AI 會逐題問你：市場 → 要不要 AI 市場重點 → **信任哪些新聞來源**（會先把預設清單
攤開給你看再問要不要增補）→ 自選股 → 技術指標 → 推播時間 → 儲存位置 → Telegram bot。
答完它就把 `config.yaml` 寫好、發測試訊息、建好每日排程。

### 2. AI 只做該做的那一段，而且要接受查核

這個專案的核心主張是：**能用程式算的，就不要讓 AI 猜。**

趨勢箭頭、收盤價、漲跌幅全部由程式計算；AI 只負責「網搜市場重點」這唯一需要
判斷力的一段——而且產出後還要再過一道**獨立的查核**。詳見下方「正確性三層防護」。

## 🚀 快速開始

```bash
git clone https://github.com/can19910531/portfolio.git
cd portfolio/stock-morning-brief
pip install -r requirements.txt
# 然後叫你的 AI 讀 docs/初始設定任務書.md（英文版 docs/SETUP_AGENT.md）
```

沒有 AI 也能手動裝：複製 `config.example.yaml` → `config.yaml`、`.env.example` → `.env`，
填一填，然後排程跑 `brief.py`。

## 🧱 架構

```
每天 08:07（系統排程器觸發，不開對話、不問權限）
   │
   └─ brief.py ─── 主流程，機械步驟全部確定性執行
         │
         ├─ charts.py ── Yahoo Finance 抓 2 年日線
         │      ├→ K線＋MACD 圖（大盤＋自選股）
         │      └→ 健檢表（短/中/長期箭頭，15 個技術訊號投票）
         │
         ├─ quotes.py ── 抓真實行情數字 ──┐
         │                                ├→ AI 產市場重點（只給網搜權限）
         │                 來源白名單 ────┘        │
         │                                         ↓
         │                                 AI 查核（獨立呼叫，逐條比對來源）
         │
         └─ notify.py ── Telegram 推播（文字先、圖後）
                └ IPv6 優先連線、自動重試、失敗寫 log、補推不重複
```

三種跑法：

| 模式 | 需要什麼 | 拿到什麼 |
|---|---|---|
| **純腳本** | Python＋排程器 | 圖表＋健檢表 |
| **AI 加值** | 再加一個會網搜的 AI CLI | 多一段市場重點（附來源） |
| **AI 加值＋查核**（預設） | 同上 | 市場重點會被逐條比對來源、標記存疑處 |

## 🛡️ 正確性三層防護

起因是一個真實事故：AI 把某則利率新聞的方向詞寫錯了。使用者是**拿這份做投資
判斷**的——**方向寫反比沒寫更糟**。於是有了這三層：

| 層 | 擋什麼 | 做法 |
|---|---|---|
| **1 事實基準** | 數字錯 | `quotes.py` 先抓好指數與自選股的收盤／日／週漲跌，塞進 prompt 明令「照抄不准自填」 |
| **2 方向詞清單** | 方向寫反 | 明列高風險對照組（升息↔降息、鷹派↔鴿派、beat↔miss…），要求寫這類詞前回頭核對原文，沒把握就整條不寫 |
| **3 查核 pass** | 漏網的錯、幻覺、來源違規 | **另開一次乾淨的 AI 呼叫**當查核員，逐條打開來源比對；有錯就改、無法證實的標 ⚠️、來源不在白名單的整條刪掉 |

三個踩過坑才學到的設計決定：

- **查核必須是獨立呼叫**——同一段 context 裡叫模型「自己再檢查一次」幾乎無效，
  它會維護自己剛寫的東西。換乾淨 context、給「查核員」而非「作者」的身分才有效。
- **查核員必須有改動門檻**——最初的查核 prompt 只寫「有錯就改成正確版本」，
  結果它把一條**原本正確**的敘述改反了。現在明訂：只有實際讀到來源相反內容才准改，
  「憑印象覺得怪」一律保持原樣並標 ⚠️。寫在 prompt 裡的原話是
  *改壞比漏抓更糟——漏抓只是少擋一個錯，改壞是親手製造一個錯，而且使用者會以為它被查核過*。
- **查核不能反過來擋掉整份晨報**——查核失敗、逾時或結果異常，一律退回原稿照常推播。

**誠實說明極限**：查核員也是 LLM，沒有即時市場知識，唯一依據是來源原文。
這套防護降低錯誤率但不可能歸零。真正的最終保險是每條都掛著的來源連結與 ⚠️ 標記。

## 📁 專案結構

```
├── brief.py                  # 主流程：畫圖 → AI 市場重點 → 查核 → 組簡報 → 推播
├── charts.py                 # 抓行情、畫圖、算健檢箭頭
├── quotes.py                 # 抓真實行情數字，當 AI 的事實基準
├── notify.py                 # Telegram 推播（文字/圖片）
├── install_schedule.ps1      # Windows 工作排程器一鍵註冊（macOS/Linux 用 cron）
├── config.example.yaml       # 設定範例（市場/自選股/指標/時間/儲存/AI/來源白名單）
├── .env.example              # Telegram 機密範例
├── requirements.txt
└── docs/
    ├── 初始設定任務書.md       # AI 安裝訪談腳本（中文）
    ├── SETUP_AGENT.md         # 同上（English）
    └── 每日晨報任務書.md       # 流程、簡報模板、三層防護的完整說明
```

## ⏰ 為什麼用系統排程器，而不是 AI 的排程功能

早期版本把整份流程交給 AI 工具內建的排程任務，結果：

- 每天開一個**新對話**，對話列表很快就被灌爆
- 遇到白名單外的指令會**跳權限詢問**，推播到手機等人按「允許」——沒人按就整份卡死
- 只在 AI 工具閒置時才觸發，錯過時間會延後好幾小時，甚至重複執行

改成系統排程器直接跑 `brief.py` 之後：不開對話、不問權限、不跳通知、
Windows 上用 `pythonw.exe` 連視窗都不會閃。AI 只在需要判斷力的那一段被呼叫，
用完就結束（`--no-session-persistence`，不留任何 session 檔）。

## 🔒 隱私與安全

- 全部在**你自己的電腦**上跑；行情來自 Yahoo Finance 公開資料，免金鑰
- Telegram token 只存在本機 `.env`（已 gitignore）
- 自選股清單存在本機 `config.yaml`（已 gitignore），不會離開你的電腦
- 預設**本機儲存**，雲端備份是可選項
- AI 呼叫只授予網搜權限（`--allowedTools WebSearch,WebFetch`），碰不到你的檔案
- **不下單、不碰金流、不給投資建議**——只做資訊彙整

## 📌 免責聲明

本工具產出為公開資訊之自動彙整，僅供參考，**非投資建議**。投資有風險，決策請自行判斷。

## License

MIT
