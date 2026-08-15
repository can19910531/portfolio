# Setup Interview Script (for your AI assistant)

> You are an AI assistant. The user just cloned **stock-morning-brief**.
> Your job: **interview the user step by step → generate `config.yaml` and `.env` → send a test push → schedule the daily run.**
> Rules: one question at a time, speak the user's language, offer defaults, never assume coding knowledge.

## Step 0 — Environment check (silent)

```bash
python --version        # needs 3.10+
pip install -r requirements.txt
```

## Step 1 — Interview (one question at a time)

### Q1 Market
Taiwan stocks (`tw`), US stocks (`us`), or `both`? → `market`

### Q2 AI market-digest section
The brief can include an **AI-written market digest** (5–8 bullets, each with a
source link). It needs a web-searching AI CLI (e.g. Claude Code) installed and
logged in on this machine. Without it the brief still ships daily — just charts
and the trend health-check.

→ `ai.enabled`
- If yes: verify the CLI runs (`claude --version`), write the command or full path to `ai.cli`
- If the CLI is not logged in, tell the user to run its login once (e.g. `claude login`) — one-time setup
- If no: skip Q3

### Q3 News sources (⚠️ always show the default list first)

Based on Q1, **list the default sources out loud** before asking anything.
Don't just ask "which sources do you want" — most users can't answer that from
a blank page, but they can react to a list.

Suggested phrasing:

> "For the market digest I'll only pull news from these sources:
>
> **Taiwan**: cnyes, UDN / Economic Daily, Yahoo Stock TW, ETtoday Finance, MoneyDJ
> **US / global**: Yahoo Finance, Reuters, CNBC
> **Primary sources** (always allowed): central bank and regulator sites, exchanges, company newsrooms
>
> Why restrict them: without a whitelist the AI will pull from personal blogs,
> content farms, aggregators that hide the original publisher, and even tool
> websites — inconsistent quality that gets in the way of your judgement.
>
> **Any sites you particularly trust and want to add?** (e.g. your broker's
> research, an industry publication.) Or just say 'defaults are fine'."

→ `ai.sources`
- Wants additions → append them (normalize to bare domains, e.g. `example.com`)
- Doesn't trust one of the defaults → remove it
- "No restriction" → set `sources: []`, but mention source quality will vary and it can be re-added anytime in `config.yaml`

### Q4 Watchlist
Which tickers? (e.g. `2330`, `0050`, `AAPL`. Taiwan symbols get `.TW` automatically; confirm names back to the user.)

### Q5 Indicators
`[kline, macd]` (default) / `[kline, macd, ma]` (adds MA5/20/60) / `[kline]`

### Q6 Notify time
What time each day? (Taiwan market opens 09:00; 08:00 recommended.)
- Nudge them off the exact hour (e.g. 08:07) — round times are everyone's scheduling peak
- With AI digest + verification enabled the run takes ~3–8 minutes; budget for it

### Q7 Storage
- `local` (default, zero dependencies): saved under `archive/`
- `none`: push to Telegram only, keep nothing
- `gdrive` (advanced): also upload to Google Drive; requires installing and authorizing the `gws` CLI, ~10 extra minutes. Only walk through this if chosen.

### Q8 Telegram
Guide the user through @BotFather (`/newbot`), collect the token, copy
`.env.example` to `.env`, fill in the token, then call `getUpdates` to resolve
the chat_id after the user messages the bot once. The token is a secret: keep it
out of anything that gets committed or shared; verify `.env` is gitignored.

## Step 2 — Generate config and verify

1. Write `config.yaml` based on the answers (see `config.example.yaml` for fields).
2. `python notify.py --text "setup test ✅"` — the user must actually receive it.
3. `python brief.py --no-push` — confirm charts, health-check and the brief appear
   under `archive/`; if AI is enabled, check the market digest section actually has
   content (empty usually means the CLI isn't logged in).
4. `python brief.py` — full run; confirm text and images arrive on Telegram.

## Step 3 — Schedule the daily run

Schedule `brief.py` alone — it orchestrates charts, AI and delivery itself.

- **Windows**:
  ```powershell
  powershell -ExecutionPolicy Bypass -File install_schedule.ps1
  ```
  Reads `notify_time` from config and registers a Task Scheduler job that runs via
  `pythonw.exe` (no console window ever appears).

- **macOS / Linux**: crontab
  ```
  7 8 * * * cd /path/to/stock-morning-brief && /usr/bin/python3 brief.py >> cron.log 2>&1
  ```

⚠️ Do **not** wire the whole flow to a conversational AI scheduler (e.g. an AI
tool's built-in scheduled tasks): it opens a fresh conversation every single day
and stalls on permission prompts nobody is there to approve. Let the OS scheduler
run `brief.py`; the AI is invoked only for the one step that needs judgement.

## Step 4 — Wrap up

- Read the final settings back to the user (market / AI on-off / source list / watchlist / indicators / time / storage).
- Tell them how to change things later: edit `config.yaml` directly, or re-run this interview.
- Point them at the logs: `last_run.log` (last run, full output) and `run_history.log` (accumulated).
- Remind them: this tool aggregates public information, it is **not investment advice**, and it never places trades.
