# Setup Interview Script (for your AI assistant)

> You are an AI assistant. The user just cloned **stock-morning-brief**.
> Your job: **interview the user step by step → generate `config.yaml` and `.env` → send a test push → schedule the daily run.**
> Rules: one question at a time, speak the user's language, offer defaults, never assume coding knowledge.

## Step 0 — Environment check (silent)

```bash
python --version        # needs 3.10+
pip install -r requirements.txt
```

## Step 1 — Interview (six questions, one at a time)

1. **Market** — Taiwan stocks (`tw`), US stocks (`us`), or `both`?
2. **Watchlist** — Which tickers to follow? (e.g. `2330`, `0050`, `AAPL`. Taiwan symbols get `.TW` automatically; confirm names back to the user.)
3. **Indicators** — `[kline, macd]` (default) / `[kline, macd, ma]` (adds MA5/20/60) / `[kline]`
4. **Notify time** — What time each day? (Taiwan market opens 09:00; 08:00 recommended.)
5. **Storage** — Where should daily reports live?
   - `local` (default, zero dependencies): saved under `archive/`
   - `none`: push to Telegram only, keep nothing
   - `gdrive` (advanced): also upload to Google Drive; requires installing and authorizing the `gws` CLI, ~10 extra minutes. Only walk through this if chosen.
6. **Telegram** — Guide the user through @BotFather (`/newbot`), collect the token, copy `.env.example` to `.env`, fill in the token, then call `getUpdates` to resolve the chat_id after the user messages the bot once. The token is a secret: keep it out of anything that gets committed or shared; verify `.env` is gitignored.

## Step 2 — Generate config and verify

1. Write `config.yaml` based on the answers (see `config.example.yaml` for fields).
2. `python notify.py --text "setup test ✅"` — the user must actually receive it.
3. `python charts.py` — confirm charts and the health-check file appear under `archive/`.
4. `python notify.py --photos "archive/<today>_圖"` — confirm images arrive.

## Step 3 — Schedule the daily run

Wire `charts.py` + `notify.py` to run at the chosen time:

- **Windows**: Task Scheduler (`schtasks /create ...`)
- **macOS/Linux**: crontab, e.g. `0 8 * * * cd <project> && python charts.py && python notify.py --photos ...`
- If the user's AI tool supports scheduled agent tasks (e.g. Claude Code scheduled tasks): schedule the AI to follow `docs/每日晨報任務書.md` instead — that adds the AI-written market-news section on top of the charts.

## Step 4 — Wrap up

- Read the final settings back to the user.
- Tell them how to change things later: edit `config.yaml` directly, or re-run this interview.
- Remind them: this tool aggregates public information, it is **not investment advice**, and it never places trades.
