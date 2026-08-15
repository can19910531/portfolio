"""
brief.py — 晨報主流程（無人值守，由系統排程器每天叫起）

設計原則：**機械步驟全部由程式確定性執行**（畫圖、算健檢、組簡報、歸檔、推播），
只有「市場重點」這一段需要判斷力，才呼叫 AI CLI 產出。
→ 不開任何對話、不跳權限詢問、不推手機通知；AI 那段掛掉也照樣推圖表與健檢。

用法：
    python brief.py                      # 正常跑（排程用這個）
    python brief.py --date 2026-08-15    # 指定日期（補跑）
    python brief.py --no-ai              # 跳過 AI 市場重點（只出健檢＋圖）
    python brief.py --no-verify          # 跳過查核 pass（省一次 AI 呼叫）
    python brief.py --no-push            # 不推播、不上雲端（本機測試）
    python brief.py --force              # 忽略 lock 檔強制執行

前提：
    1. config.yaml 已設定（或讓 AI 讀 docs/初始設定任務書.md 幫你產生）
    2. .env 內有 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
    3. 想要「市場重點」段：需安裝並登入一個會網搜的 AI CLI（預設 claude）

流程與模板的完整說明見 docs/每日晨報任務書.md。
"""
import argparse
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

HERE = Path(__file__).parent

# 排程可用 pythonw.exe 執行（Windows 上完全無視窗），此時 sys.stdout 是 None，
# print 會直接爆掉 → 把整段輸出導進 last_run.log（每次覆寫），除錯看這支就好。
if sys.stdout is None or sys.stderr is None:
    _console = open(HERE / "last_run.log", "w", encoding="utf-8", errors="replace")
    sys.stdout = sys.stderr = _console
elif hasattr(sys.stdout, "reconfigure"):  # 有 console 時預設可能是系統編碼，中文會炸
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

LOCKFILE = HERE / ".brief.lock"
LOCK_STALE_HOURS = 3   # 超過這時數的 lock 視為殘留（上次跑到一半當掉）
AI_TIMEOUT = 900       # 單次 AI 呼叫上限（秒）
DRAW_TIMEOUT = 900     # charts.py 上限（秒）

WEEKDAY_ZH = "一二三四五六日"


def load_config() -> dict:
    f = HERE / "config.yaml"
    if not f.exists():
        print("X 找不到 config.yaml。請複製 config.example.yaml 改名為 config.yaml，"
              "或讓你的 AI 讀 docs/初始設定任務書.md 幫你完成設定。")
        sys.exit(2)
    cfg = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    cfg.setdefault("market", "both")
    cfg.setdefault("storage", "local")
    cfg.setdefault("archive_dir", "./archive")
    ai = cfg.setdefault("ai", {}) or {}
    ai.setdefault("enabled", True)
    ai.setdefault("cli", "claude")
    ai.setdefault("model", "sonnet")
    ai.setdefault("verify", True)
    ai.setdefault("sources", [])
    cfg["ai"] = ai
    return cfg


CFG = load_config()
ARCHIVE = (HERE / CFG["archive_dir"]).resolve()
LOGFILE = HERE / "run_history.log"


def log(msg: str):
    """同時印出＋寫進 run_history.log（排程無人看畫面，全靠這個追）"""
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with LOGFILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── lock：避免排程補觸發造成兩份同時跑（推播順序會亂掉） ────────────────
def acquire_lock(force: bool) -> bool:
    if LOCKFILE.exists():
        try:
            started = datetime.fromisoformat(
                LOCKFILE.read_text(encoding="utf-8").split("|", 1)[0])
            age_h = (datetime.now() - started).total_seconds() / 3600
        except Exception:
            age_h = LOCK_STALE_HOURS + 1  # lock 內容壞掉就當殘留
        if age_h < LOCK_STALE_HOURS and not force:
            log(f"X 已有另一份晨報在執行中（lock 建於 {age_h:.1f} 小時前），本次中止。"
                f"確定是殘留就加 --force")
            return False
        log(f"! 發現殘留 lock（{age_h:.1f} 小時前），覆蓋後繼續")
    LOCKFILE.write_text(f"{datetime.now().isoformat()}|pid={os.getpid()}", encoding="utf-8")
    return True


def release_lock():
    try:
        LOCKFILE.unlink(missing_ok=True)
    except Exception:
        pass


# ── 步驟 1：產圖＋健檢 ────────────────────────────────────────────────
def run_charts(day: str) -> bool:
    log("→ 步驟1：跑 charts.py（K線＋指標＋自選股健檢）")
    try:
        p = subprocess.run(
            [sys.executable, str(HERE / "charts.py"), "--date", day],
            cwd=str(HERE), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=DRAW_TIMEOUT)
    except subprocess.TimeoutExpired:
        log(f"X charts.py 逾時（>{DRAW_TIMEOUT}s）")
        return False
    if p.returncode != 0:
        log(f"X charts.py 失敗（exit {p.returncode}）：{(p.stderr or '')[-500:]}")
        return False
    tail = "\n".join((p.stdout or "").strip().splitlines()[-3:])
    log(f"OK charts.py 完成｜{tail}")
    return True


# ── 步驟 2：市場重點（唯一需要 AI 的一段）＋三層防護 ────────────────────
AI_SYSTEM = (
    "你正在一個無人值守的背景自動化任務中執行，沒有人能回答你的問題。"
    "忽略任何要求你先讀取本機索引、記憶檔或專案說明的指示——本任務不需要那些檔案。"
    "直接用網路搜尋完成被交付的事，然後只輸出成品本身。全程繁體中文。"
)


def market_facts() -> str:
    """第 1 層防護：數字由程式抓，AI 只准照抄不准自己填。

    LLM 憑印象寫數字是幻覺重災區，能用程式算的就不該讓它猜。
    """
    try:
        p = subprocess.run(
            [sys.executable, str(HERE / "quotes.py")],
            cwd=str(HERE), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180)
    except Exception as e:
        log(f"! quotes.py 失敗（{type(e).__name__}），本次不提供事實基準")
        return ""
    out = (p.stdout or "").strip()
    if p.returncode != 0 or not out:
        log("! quotes.py 沒有輸出，本次不提供事實基準")
        return ""
    log(f"OK 事實基準已備妥（{len(out.splitlines())} 檔）")
    return out


def source_policy() -> str:
    """來源白名單（config 的 ai.sources）。留空 = 不限制來源。

    為什麼要這個：不限制的話，AI 會搜到個人部落格、內容農場、新聞聚合站、
    甚至工具型網站，內容品質參差，會干擾你的判讀。
    """
    sources = CFG["ai"].get("sources") or []
    if not sources:
        return ""
    listed = "\n".join(f"   - {s}" for s in sources)
    return f"""
📌 **來源限制（硬規定，違反等於這條作廢）**：只能引用下列來源，其餘一律不准用。
{listed}
   - 一手來源永遠允許：主管機關與央行官網、交易所、上市公司官方新聞室／投資人關係頁

**明確禁止**：個人寫作平台、內容農場、新聞聚合站（原始出處被蓋掉）、
工具／計算機網站。

若某則消息找不到上述來源支持，**就不要寫那條**——寧可只出 4 條紮實的，
也不要為了湊數去引用來路不明的網站。
"""


# 第 2 層防護：方向詞在「壓縮成一句話」時最容易翻轉，一個字之差意思相反。
# ⚠️ 這裡只要求「回頭核對原文」，**不預設哪個方向才對**——若因為直覺認定
# 「應該是降息」而去改，反而會親手造出錯誤。判斷依據只能是來源原文。
DIRECTION_GUARD = """
⚠️ **方向詞是最容易出錯的地方**，每次要寫下面這類詞之前，回頭確認來源原文怎麼寫的：
   升息(rate hike) ↔ 降息(rate cut)｜鷹派(hawkish) ↔ 鴿派(dovish)
   上漲 ↔ 下跌｜擴大 ↔ 收斂｜年增 ↔ 年減｜流入 ↔ 流出
   優於預期(beat) ↔ 低於預期(miss)｜上修 ↔ 下修
   方向沒把握就整條不要寫，寧可少一條也不要寫反——使用者會拿這份做判斷。
"""


def build_ai_prompt(day: str, weekday: int) -> str:
    holiday = weekday >= 5  # 週末休市
    market = str(CFG["market"]).lower()
    scope_map = {
        "tw": "台股大盤動態＋美股隔夜對台股的影響",
        "us": "美股四大指數動態＋盤後重要消息",
        "both": "台股大盤動態＋美股隔夜（四大指數一句話帶過）",
    }
    if holiday:
        scope = ("- 這是休市日，改寫「本週回顧＋下週展望」\n"
                 "- 涵蓋：本週大盤走勢一句話、本週市場表現、下週值得盯的事件或數據\n"
                 "- 新聞可放寬到 72 小時內")
    else:
        scope = (f"- {scope_map.get(market, scope_map['both'])}\n"
                 "- **現在市場的熱門趨勢／題材**（哪些族群在轉強、資金流向哪、"
                 "大家在討論什麼）——這是使用者最想看的\n"
                 "- 影響今天開盤的 1~2 個關鍵變數\n"
                 "- 只取 24 小時內的新聞")

    facts = market_facts()
    facts_block = (
        "\n【以下行情由程式抓自 Yahoo Finance，是**事實基準**。凡提到這些標的的收盤價、"
        "日／週漲跌幅，一律照抄這裡的數字，不可自行改寫或憑印象填入】\n"
        f"```\n{facts}\n```\n"
    ) if facts else ""

    return (
        f"今天是 {day}（週{WEEKDAY_ZH[weekday]}）。\n"
        f"請用網路搜尋，整理市場重點，濃縮成 5~8 條 bullet。\n"
        f"{facts_block}\n"
        f"內容範圍：\n{scope}\n\n"
        "格式規則（務必嚴格遵守）：\n"
        "- 每條格式固定為：`- {重點，一句話}｜[來源](網址)`\n"
        "- 每條**必附真實來源連結**；沒有可靠來源的就不要寫\n"
        "- **禁止憑空編造任何數字**；查不到就少寫幾條，寧缺勿假\n"
        "- 重要的排前面\n"
        "- 不下判斷、不喊買賣、不給投資建議，只陳述事實與市場說法\n"
        f"{source_policy()}"
        f"{DIRECTION_GUARD}\n"
        "輸出規則：**只輸出那幾條 bullet 本身**，不要標題、不要開場白、不要結語、"
        "不要說明你做了什麼。你的整段輸出會被程式原封不動貼進簡報裡。"
    )


# AI 因認證問題失敗時，要在簡報裡直接點名（使用者只看推播、不會去翻 log）
_auth_failed = False


def run_ai(prompt: str, what: str) -> str | None:
    """跑一次 AI CLI（背景、無對話、不留 session）；失敗回 None"""
    global _auth_failed
    ai = CFG["ai"]
    cmd = [
        str(ai["cli"]), "-p", prompt,   # prompt 當參數傳，不靠 stdin
        "--output-format", "text",
        "--model", str(ai["model"]),
        # ⚠️ 不要設 --fallback-model 成與 --model 相同的值，CLI 會直接報錯退出
        "--allowedTools", "WebSearch,WebFetch",   # 精準白名單：只給網搜
        "--strict-mcp-config",                    # 不載入任何 MCP，啟動快
        "--no-session-persistence",               # 不寫 session 檔＝不留對話紀錄
        "--append-system-prompt", AI_SYSTEM,
    ]
    try:
        p = subprocess.run(
            cmd, cwd=str(HERE), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=AI_TIMEOUT)
    except FileNotFoundError:
        log(f"X 找不到 AI CLI「{ai['cli']}」——請確認已安裝，或改 config.yaml 的 ai.cli")
        _auth_failed = True
        return None
    except subprocess.TimeoutExpired:
        log(f"X {what}逾時（>{AI_TIMEOUT}s）")
        return None
    if p.returncode != 0:
        err = (p.stderr or "") + (p.stdout or "")
        log(f"X {what}失敗（exit {p.returncode}）：{err[-400:]}")
        if any(k in err.lower() for k in ("authenticate", "oauth", "unauthorized",
                                          "401", "not logged in")):
            _auth_failed = True
        return None
    return (p.stdout or "").strip()


def _bullets(text: str) -> list[str]:
    return [ln.rstrip() for ln in text.splitlines() if ln.strip().startswith(("-", "•", "*"))]


def fetch_market_notes(day: str, weekday: int) -> str | None:
    log("→ 步驟2：呼叫 AI 產市場重點（背景、無對話、不留 session）")
    text = run_ai(build_ai_prompt(day, weekday), "市場重點")
    if text is None:
        return None
    bullets = _bullets(text)
    if len(bullets) < 3:
        log(f"X 回傳內容不像 bullet 清單（{len(bullets)} 條），棄用。原文前200字：{text[:200]}")
        return None
    log(f"OK 市場重點 {len(bullets)} 條")
    return "\n".join(bullets)


# 第 3 層防護：查核 pass。生成與查核**分成兩次獨立呼叫**——同一次生成裡叫它
# 「自己檢查一遍」幾乎沒用（模型會維護自己剛寫的東西），換一次乾淨的 context
# 專做比對才抓得到。
VERIFY_PROMPT = """你是一份即將寄給投資人的晨報的**查核員**，不是作者。

請逐條打開下面清單裡的來源連結，實際比對內容，專門抓這幾種錯：
1. **方向詞寫反**——升息/降息、鷹派/鴿派、上漲/下跌、擴大/收斂、年增/年減、
   優於預期/低於預期、上修/下修、流入/流出
2. **數字與來源不符**——指數點數、漲跌幅、百分比、日期
3. **來源根本沒提到該敘述**（幻覺）
4. **來源不在允許清單內**（若下方有列出來源限制）

⛔ **改動門檻（最重要，違反會造成比原稿更嚴重的錯誤）**：
   只有在你**實際打開來源、讀到與敘述明確相反的內容**時才可以改。
   「我覺得應該是相反的」「憑印象這裡怪怪的」「一般來說不會這樣」——
   **這些都不構成修改理由**，一律保持原樣並標 ⚠️ 讓使用者自行判斷。
   你沒有即時市場知識，你唯一的依據是來源原文。**改壞比漏抓更糟**：
   漏抓只是少擋一個錯，改壞是親手製造一個錯，而且使用者會以為它被查核過。

輸出規則（務必嚴格遵守）：
- 回傳**修正後的完整清單**，格式與輸入完全一致：`- {一句話}｜[來源](網址)`
- 順序不變
- 確認無誤的條目**原文照抄，一個字都不要改**
- 確實與來源牴觸的條目，才改成與來源相符的版本（照抄來源用詞，不要自己潤飾）
- 打不開來源、或來源無法證實該敘述的條目，在句末（｜之前）加上 ` ⚠️`
- 來源不在允許清單內的條目，整條刪掉（不要試著找替代來源，直接刪）
- **只輸出清單本身**，不要說明你改了什麼、不要標題、不要結語
"""
# 注意：VERIFY_PROMPT 內含 `{一句話}` 這種大括號範例，**不可以用 .format() 組裝**
# （會被當成佔位符而 KeyError），一律用字串相加。
VERIFY_TAIL = "\n待查核清單：\n"


def verify_notes(notes: str) -> str:
    """查核失敗就原封不動用原稿——查核是加分項，不能反過來擋掉整份晨報。"""
    log("→ 步驟2b：查核市場重點（回頭比對來源，抓方向詞與數字錯誤）")
    original = _bullets(notes)
    out = run_ai(VERIFY_PROMPT + source_policy() + VERIFY_TAIL + notes, "查核")
    if out is None:
        log("! 查核沒跑成，改用未查核的原稿")
        return notes
    fixed = _bullets(out)
    # 查核可以刪掉來源違規的條目，所以不跟原稿比條數；但少於 3 條就沒有簡報價值了
    if len(fixed) < 3:
        log(f"! 查核後只剩 {len(fixed)} 條（原 {len(original)} 條），太少，改用原稿")
        return notes
    changed = sum(1 for a, b in zip(original, fixed) if a.strip() != b.strip())
    flagged = sum(1 for b in fixed if "⚠️" in b)
    log(f"OK 查核完成：{len(fixed)} 條"
        f"（改動 {changed}、標記 {flagged}、剔除 {len(original) - len(fixed)}）")
    return "\n".join(fixed)


# ── 步驟 3：組簡報 ────────────────────────────────────────────────────
FALLBACK_NOTES = (
    "- ⚠️ 今日市場重點自動蒐集失敗（AI 服務或網路異常），以下僅提供健檢與技術圖表\n"
    "- 詳細失敗原因記在 `run_history.log`"
)
FALLBACK_AUTH = (
    "- ⚠️ 今日市場重點蒐集失敗：**AI CLI 未登入或登入已過期**\n"
    "- 請在終端機重新登入你的 AI CLI（例如 `claude login`）即可恢復\n"
    "- 以下健檢與技術圖表不受影響"
)


def _has_holdings(text: str) -> bool:
    """健檢表裡有沒有真的資料（而不是只剩標題）"""
    return any("｜收 " in ln for ln in text.splitlines())


def load_health(day: str) -> str:
    """讀今日健檢；若行情源抓不到資料導致整表空掉（限流時會發生），
    退回最近一份有資料的健檢並註明日期——空白的健檢區對使用者毫無價值。"""
    today = ARCHIVE / f"{day}_健檢.md"
    if today.exists():
        txt = today.read_text(encoding="utf-8")
        if _has_holdings(txt):
            return txt
    d = date.fromisoformat(day)
    for i in range(1, 8):
        prev = (d - timedelta(days=i)).isoformat()
        f = ARCHIVE / f"{prev}_健檢.md"
        if f.exists():
            txt = f.read_text(encoding="utf-8")
            if _has_holdings(txt):
                log(f"! 今日健檢無資料（行情抓取失敗），改用 {prev} 的健檢")
                return txt.rstrip() + f"\n\n⚠️ 今日行情讀取失敗，以上為 {prev} 的健檢結果，僅供參考"
    log("! 找不到任何可用的健檢資料，簡報該區塊將留白")
    return ""


def build_brief(day: str, weekday: int, notes: str | None, health: str) -> str:
    holiday = weekday >= 5
    fallback = FALLBACK_AUTH if _auth_failed else FALLBACK_NOTES
    title = f"# 📈 股票晨報 {day}（週{WEEKDAY_ZH[weekday]}）" + ("（假日版）" if holiday else "")
    section = "## 🔥 本週回顧／下週展望" if holiday else "## 🔥 市場重點"
    # 查核標記過的條目要講清楚意思，否則使用者只會看到一個莫名的 ⚠️
    hint = ("\n_（標 ⚠️ 者為查核時無法從來源證實，請點連結自行確認）_"
            if notes and "⚠️" in notes else "")
    return "\n".join([
        title, "",
        section,
        notes or fallback, hint, "",
        health.strip() or "（今日健檢產出失敗，請查 run_history.log）", "",
        "（K線圖隨後送達 📊）", "",
        "---",
        "📌 本簡報為 AI 自動彙整之公開資訊，僅供參考，**非投資建議**。", "",
    ])


# ── 步驟 4：歸檔＋推播 ────────────────────────────────────────────────
def upload_drive(brief_file: Path) -> bool:
    """storage: gdrive 才會走這段（需自行安裝並授權 gws CLI）"""
    folder = CFG.get("gdrive_folder_id")
    if not folder:
        log("! storage 設為 gdrive 但沒填 gdrive_folder_id，略過上傳")
        return False
    gws = str(CFG.get("gws_path", "gws"))
    log("→ 步驟4：上傳雲端歸檔")
    try:
        p = subprocess.run(
            [gws, "drive", "+upload", str(brief_file), "--parent", folder],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=180)
    except Exception as e:
        log(f"X 上傳雲端失敗：{type(e).__name__}: {e}")
        return False
    if p.returncode != 0:
        log(f"X 上傳雲端失敗（exit {p.returncode}）：{(p.stderr or p.stdout or '')[-300:]}")
        return False
    log("OK 已上傳雲端")
    return True


def push(args_: list[str], what: str) -> bool:
    try:
        p = subprocess.run(
            [sys.executable, str(HERE / "notify.py"), *args_],
            cwd=str(HERE), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=3600)
    except subprocess.TimeoutExpired:
        log(f"X {what}推播逾時")
        return False
    if p.returncode != 0:
        log(f"X {what}推播失敗：{((p.stdout or '') + (p.stderr or ''))[-300:]}")
        return False
    log(f"OK {what}推播成功")
    return True


# ── 主流程 ────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="股票晨報主流程（無人值守）")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--no-ai", action="store_true", help="跳過 AI 市場重點")
    ap.add_argument("--no-verify", action="store_true", help="跳過查核 pass")
    ap.add_argument("--no-push", action="store_true", help="不推播、不上雲端")
    ap.add_argument("--force", action="store_true", help="忽略 lock 強制執行")
    a = ap.parse_args()

    day = a.date
    weekday = date.fromisoformat(day).weekday()

    if not acquire_lock(a.force):
        return 1
    try:
        log(f"===== 晨報開始 {day}（週{WEEKDAY_ZH[weekday]}）=====")
        ok_charts = run_charts(day)
        health = load_health(day)

        use_ai = CFG["ai"]["enabled"] and not a.no_ai
        notes = fetch_market_notes(day, weekday) if use_ai else None
        if notes and CFG["ai"]["verify"] and not a.no_verify:
            notes = verify_notes(notes)

        brief_file = ARCHIVE / f"{day}_晨報.md"
        brief_file.parent.mkdir(parents=True, exist_ok=True)
        brief_file.write_text(build_brief(day, weekday, notes, health), encoding="utf-8")
        log(f"OK 步驟3：簡報已存 {brief_file.name}")

        if a.no_push:
            log("（--no-push：略過上雲端與推播）")
            log("===== 晨報結束（本機測試模式）=====")
            return 0

        if str(CFG["storage"]).lower() == "gdrive":
            upload_drive(brief_file)

        # 順序固定：文字先、圖後
        ok_text = push([str(brief_file)], "文字")
        imgdir = ARCHIVE / f"{day}_圖"
        ok_photo = push(["--photos", str(imgdir)], "圖表") if imgdir.is_dir() else False
        if not imgdir.is_dir():
            log(f"X 找不到圖片資料夾 {imgdir.name}，略過圖表推播")

        checks = [("畫圖", ok_charts), ("文字推播", ok_text), ("圖表推播", ok_photo)]
        if use_ai:
            checks.append(("市場重點", notes is not None))
        bad = [n for n, ok in checks if not ok]
        log(f"⚠ 晨報完成但有缺項：{'、'.join(bad)}" if bad else "✅ 晨報全部完成")
        log("===== 晨報結束 =====")
        return 0 if not bad else 2
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
