# 🗂️ 作品集 Portfolio

AI × 自動化的實作作品。每個資料夾是一個獨立作品，含完整程式碼與安裝說明。

| 時間 | 作品 | 說明 | 技術 |
|---|---|---|---|
| 202608 | [📈 stock-morning-brief](stock-morning-brief/) | AI 股票晨報機器人：每天自動網搜市場重點、技術面健檢自選股、產 K線+MACD 圖，推播到 Telegram。兩個設計重點——**① 用 AI 訪談完成安裝**（clone 後叫你的 AI 讀任務書，逐題問完自動設定好）；**② 正確性三層防護**（程式提供事實基準、方向詞護欄、獨立的 AI 查核 pass 逐條比對來源），因為這份報告是要拿來做判斷的。 | Python · yfinance · mplfinance · Telegram Bot API · AI agent 任務書 · LLM 幻覺防護 |
| 202608 | [🦶 foot-massage-map](foot-massage-map/) | 給長輩的腳底按摩 3D 穴道對照網頁：卡通 3D 雙腳單指旋轉，點反射區跳出大字說明（對應器官、飲食與運動建議），47 個反射區依衛福部反射區圖整理，iPhone 大字介面。**[線上體驗](https://foot-massage-map.pages.dev)** | Three.js · 程式化 3D 建模 · 卡通渲染 · Cloudflare Pages |

> 陸續更新中。

## 怎麼使用這些作品

```bash
git clone https://github.com/can19910531/portfolio.git
cd portfolio/stock-morning-brief   # 進入想用的作品
```

每個作品的 README 都有自己的快速開始；多數作品支援「叫你的 AI 讀 docs 裡的任務書」完成設定。

## License

MIT
