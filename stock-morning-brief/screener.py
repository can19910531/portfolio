"""
screener.py — 每週選股觀察名單（週線長線；brief.py 週六呼叫）⚠️ 目前只支援台股

三道關卡，全過才列出（寧缺勿濫）：
    ① 技術面（已回測，見 research/results_weekly.md）
         週收創 52 週新高 ＋ 週收 > 40 週線 ＋ 站上 5 週線、週 MACD 快線 > 0、週 K ≥ 70
    ② 籌碼面（初步分析，未長期驗證）：400 張以上大戶持股比例比上一週增加超過 0.5 個百分點
       （依 research/results_big_holders.md 由「>0」提高；增加 0～0.5 的那群反而落後）
    ③ 新聞面（未經回測驗證）：AI 查近兩週白名單來源新聞，有明確利空就剔除

另外：
    - 樂透區（趨勢轉強，統計上無優勢）：同樣過三關，標明樂透性質（config 可關）
    - 觀察名單追蹤：列過的股票每週檢查，觸及停損或週收跌破出場線（主 20 週、樂透 10 週）就發出場提醒
      （沒空天天盯盤的人，出場提醒比進場訊號更重要）

停損價、出場線全部由程式依規則計算；AI 只做新聞關，不碰任何數字。
產出的是「符合規則的名單」，不是買賣建議。

用法：
    python screener.py                     # 產本週選股 → <archive_dir>/YYYY-MM-DD_選股.md
    python screener.py --no-ai             # 跳過新聞關（測試用；新聞關沒過的一律不列）
    python screener.py --date 2026-10-24   # 指定執行日（只會用該日之前已收完的週K）
"""
import argparse
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import backtest as bt            # noqa: E402
import big_holders               # noqa: E402
import brief                     # noqa: E402  共用 config、AI CLI 呼叫、來源白名單

CFG = brief.CFG
SC = CFG.get("screener") or {}
ARCHIVE = brief.ARCHIVE
TRACK = HERE / "data" / "watchlist_tracking.csv"
SCORECARD = HERE / "research" / "results_weekly.json"
TOP_N = int(SC.get("top_n", 200))
MAX_MAIN = int(SC.get("max_main", 5))
MAX_LOTTERY = int(SC.get("max_lottery", 3)) if SC.get("lottery", True) else 0
TRIGGER = 52                            # 創 52 週新高（回測選出的規則）
CHIP_MIN = 0.5                          # 大戶關門檻（百分點）：Δ > 0.5 才過（research/pre_registration.md 修訂二）
EXIT_MA = {"主": 20, "樂透": 10}      # 出場線：與回測一致（主＝跌破 20 週線、樂透＝跌破 10 週線）
TWSE_DAY = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_DAY = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
MONEYDJ_HOLD = "https://www.moneydj.com/ETF/X/Basic/Basic0007B.xdjhtm?etfid={}.TW"


def log(msg: str):
    print(msg, flush=True)


# ── 股票池：目前市值前 N 大 ＋ 自選股裡 ETF 的成分股 ＋ 追蹤中的股票 ──────────
def current_top(meta: pd.DataFrame, n: int) -> list[str]:
    px = {}
    for d in bt._get_json(TWSE_DAY):
        px[d["Code"].strip()] = bt._num(d.get("ClosingPrice"))
    for d in bt._get_json(TPEX_DAY):
        px[d["SecuritiesCompanyCode"].strip()] = bt._num(d.get("Close"))
    mcap = {c: px[c] * s for c, s in meta["shares"].items() if px.get(c)}
    return [c for c, _ in sorted(mcap.items(), key=lambda kv: -kv[1])[:n]]


def watchlist_etf_constituents() -> set:
    """config 自選股中台股 ETF（00 開頭）的成分股；抓不到就略過（不影響主流程）"""
    if not SC.get("etf_constituents", True):
        return set()
    etfs = [str(w.get("symbol", "")).strip() for w in CFG.get("watchlist", []) or []
            if str(w.get("market", "tw")).lower() == "tw" and str(w.get("symbol", "")).startswith("00")]
    out = set()
    for etf in etfs:
        try:
            req = urllib.request.Request(MONEYDJ_HOLD.format(etf), headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                html = r.read().decode("utf-8", "ignore")
            codes = {c for c in re.findall(r"(\d{4})\.TW", html) if not c.startswith("00")}
            out |= codes
            log(f"  {etf} 成分股 {len(codes)} 檔")
        except Exception as e:
            log(f"  ! {etf} 成分股抓取失敗（{type(e).__name__}），略過")
    return out


def load_track() -> pd.DataFrame:
    cols = ["listed", "code", "name", "section", "ref_close", "stop", "status", "closed", "note"]
    if TRACK.exists():
        # 文字欄一律指定 str：note 過去全空會被讀成 float，第一次寫「觸及停損」就當掉
        return pd.read_csv(TRACK, dtype={c: str for c in ("listed", "code", "name", "section", "status", "closed", "note")}).reindex(columns=cols)
    return pd.DataFrame(columns=cols)


def chip_table(codes: list[str]) -> tuple[dict, str]:
    """這些股票在集保官網「最新一週 vs 上一週」的 400 張以上持股比例與增減（百分點）。
    回傳 ({code: (pct, chg)}, 最新週)。官網連不上時退回存檔裡最新的兩週，並在 log 註明"""
    store = big_holders.load_store()
    try:
        w0, w1 = big_holders.ensure_recent(codes, 2)
        store = big_holders.load_store()
    except Exception as e:
        log(f"! 集保官網連不上（{type(e).__name__}），改用存檔裡最新的兩週")
        weeks = sorted(store["date"].unique(), reverse=True)
        if len(weeks) < 2:
            return {}, ""
        w0, w1 = weeks[0], weeks[1]
    pct = store.pivot_table(index="code", columns="date", values="pct_400", aggfunc="last")
    out = {}
    for c in codes:
        if c in pct.index and w0 in pct.columns and w1 in pct.columns:
            a, b = pct.at[c, w0], pct.at[c, w1]
            if pd.notna(a) and pd.notna(b):
                out[c] = (float(a), float(a - b))
    return out, w0


# ── 新聞關（唯一用到 AI 的地方）───────────────────────────────────────────
NEWS_PROMPT = """請逐檔查詢下列股票**近 14 天**的新聞，只判斷一件事：有沒有「明確利空」。

{stocks}

明確利空（符合任一項才算）：
- 營收或獲利明顯衰退、下修財測
- 被列為處置股或注意股
- 重大訴訟、罰款、弊案、主管機關調查
- 大股東或董監事大量申報轉讓持股
- 主要客戶流失、砍單、停工或重大災害
- 會計師出具非無保留意見

**不算利空**：單純股價下跌、外資或法人調節、券商調降目標價（除非伴隨上面的基本面事件）。

輸出格式（每檔一行，固定格式，不要多也不要少）：
代號｜利空 或 無明顯利空｜一句話重點｜[來源](網址)

- 判定為利空時，一句話寫出是哪個事件，並附來源
- 無明顯利空時，一句話寫近期最主要的一則新聞
- **一句話重點裡不准寫任何數字**（營收、百分比、價格、日期都不要），只寫事件本身——
  數字讓使用者自己點來源看，你寫的數字無法被驗證
- 若下方有來源限制，只能引用清單內來源；找不到這檔的新聞就寫「近兩週無相關新聞」，來源寫「—」
- 不要在輸出裡解釋來源或你的判斷過程；只輸出這幾行本身，不要標題、不要說明
"""

NEWS_VERIFY = """你是查核員，不是作者。下面是另一個 AI 對幾檔股票的新聞判定。
請逐行打開來源連結，確認「判定」與「一句話重點」都與來源相符。

⛔ 改動門檻：只有實際讀到來源與敘述明確相反時才可以改（例如來源其實是利多卻判成利空，
或重點的方向詞寫反）。「覺得怪怪的」不構成修改理由，保持原樣並在重點句末加 ⚠️。
來源打不開或無法證實的，在重點句末加 ⚠️。
**需要標記時只加一個 ⚠️ 符號，不准在後面寫任何說明文字。**
重點句裡如果有數字，刪掉數字、只留事件本身。

輸出：照原格式回傳全部行（代號｜判定｜一句話重點｜[來源](網址)），只輸出這幾行。
"""

LINE_RE = re.compile(r"^\s*[-•*]?\s*(\d{4}[A-Z]?)\s*｜\s*(利空|無明顯利空)\s*｜\s*(.+?)\s*$")
NOTE_MAX = 60


def _clean_note(rest: str) -> str:
    """防 AI 不聽話：⚠️ 後面的說明文字砍掉、過長截斷，來源連結保留"""
    text, _, src = rest.rpartition("｜")
    if not text:
        text, src = rest, "—"
    warn = "⚠️" in text
    text = re.sub(r"[（(][^）)]*[）)]", "", text.split("⚠️")[0]).strip(" 。，")
    if len(text) > NOTE_MAX:
        text = text[:NOTE_MAX] + "…"
    return f"{text}{' ⚠️' if warn else ''}｜{src.strip()}"


def _parse_news(text: str) -> dict:
    out = {}
    for ln in (text or "").splitlines():
        m = LINE_RE.match(ln)
        if m:
            out[m.group(1)] = (m.group(2), _clean_note(m.group(3)))
    return out


def news_gate(cands: list[tuple[str, str]]) -> dict | None:
    """回傳 {code: (判定, 重點與來源)}；AI 整個失敗回 None"""
    stocks = "\n".join(f"- {c} {n}" for c, n in cands)
    prompt = NEWS_PROMPT.format(stocks=stocks) + brief.source_policy() + brief.DIRECTION_GUARD
    log(f"→ 新聞關：AI 檢查 {len(cands)} 檔")
    raw = brief.run_ai(prompt, "選股新聞檢查")
    first = _parse_news(raw)
    if not first:
        return None
    checked = brief.run_ai(NEWS_VERIFY + brief.source_policy() + "\n待查核：\n" + raw, "選股新聞查核")
    second = _parse_news(checked)
    if second and set(second) >= set(first):     # 查核要涵蓋同樣的股票才採用
        log(f"OK 新聞查核完成（{len(second)} 檔）")
        return second
    log("! 新聞查核沒跑成或不完整，改用未查核的判定")
    return first


def scorecard_lines() -> list[str]:
    try:
        r = json.loads(SCORECARD.read_text(encoding="utf-8"))
        o, p = r["user"]["OOS"], r["portfolio"]["user"]["OOS"]
        return [
            f"- 回測（2023 起樣本外）：勝率 {o['win']:.0%}、**一半以上會停損**；"
            f"每筆比同期大型股平均多 {o['excess']:+.1%}（統計上確立）",
            f"- ⚠️ 但整體輸給直接抱 0050：組合年化 {p['strat']['cagr']:+.0%} vs 0050 "
            f"{p['hold']['cagr']:+.0%}，最大回撤 {p['strat']['mdd']:.0%} vs {p['hold']['mdd']:.0%}",
            f"- 大戶關（增加 > {CHIP_MIN} 個百分點）：只有一年資料的初步分析；新聞關：未經回測驗證",
        ]
    except Exception:
        return ["- （成績單讀取失敗，見 research/results_weekly.md）"]


def fmt_px(x: float) -> str:
    return f"{x:,.2f}" if x < 100 else f"{x:,.0f}" if x >= 1000 else f"{x:,.1f}"


def main() -> int:
    ap = argparse.ArgumentParser(description="每週選股觀察名單（台股）")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--no-ai", action="store_true", help="跳過新聞關（新聞關未過一律不列）")
    a = ap.parse_args()
    run_day = pd.Timestamp(a.date)
    use_ai = CFG["ai"]["enabled"] and not a.no_ai

    meta = bt.fetch_meta()
    top = current_top(meta, TOP_N)
    track = load_track()
    active = track[track["status"] == "觀察中"]
    extra = sorted((watchlist_etf_constituents() | set(active["code"])) - set(top))
    universe = top + extra
    log(f"→ 股票池：前 {TOP_N} 大 ＋ ETF 成分股／追蹤中 額外 {len(extra)} 檔")

    start = (run_day - pd.Timedelta(days=365 * 3)).strftime("%Y-%m-%d")
    m = bt.load_market(start=start, codes=universe, tag="scan", max_age_hours=6)
    w = bt.to_weekly(m)
    done = w["C"].index[w["C"].index < run_day.normalize()]
    w = {k: (v.loc[done] if isinstance(v, pd.DataFrame) and k != "meta" else v) for k, v in w.items()}
    ind = bt.indicators(w, **bt.WEEKLY)
    ind["top"] = ind["C"].notna()
    wk = ind["C"].index[-1]
    last_day = m["C"].index[m["C"].index < run_day.normalize()][-1]
    log(f"→ 使用週K：{wk.date()} 當週（日線資料到 {last_day.date()}）")
    if (wk - last_day).days > 1:
        log(f"! 本週日線只到 {last_day.date()}，週五資料可能還沒更新——週K 可能不完整")

    names = meta["name"].to_dict()
    C, L = ind["C"].iloc[-1], ind["L"]
    atr = ind["atr"].iloc[-1]
    exit_ma = {sec: ind["ma"][n] for sec, n in EXIT_MA.items()}
    rs = ind["rs"].iloc[-1].fillna(0)

    main_sig = bt.weekly_signal(ind, TRIGGER, h1=True).iloc[-1]
    lot_sig = bt.lottery_weekly(ind).iloc[-1] & ~main_sig
    tech = {"主": list(main_sig.index[main_sig.fillna(False)]),
            "樂透": list(lot_sig.index[lot_sig.fillna(False)]) if MAX_LOTTERY else []}
    for sec, codes in tech.items():
        log(f"  技術面通過（{sec}）：{'、'.join(f'{c}{names.get(c, c)}' for c in codes) or '無'}")

    chips, chip_week = chip_table(sorted({c for codes in tech.values() for c in codes}))
    funnel, passed_chip = {}, {}
    for sec, codes in tech.items():
        ok = [c for c in codes if c in chips and pd.notna(chips[c][1]) and chips[c][1] > CHIP_MIN]
        passed_chip[sec] = sorted(ok, key=lambda c: -rs.get(c, 0))
        funnel[sec] = [len(codes), len(ok)]

    cands = [(c, names.get(c, c)) for sec in passed_chip for c in passed_chip[sec]]
    news, news_failed = {}, False
    if cands and use_ai:
        res = news_gate(cands)
        if res is None:
            news_failed = True
            log("X 新聞關失敗：依寧缺勿濫，本週不列出任何股票")
        else:
            news = res
    final = {}
    for sec in passed_chip:
        if not use_ai:   # 沒有 AI CLI 的使用者：新聞關略過（報告會標明），否則永遠收不到選股
            keep = list(passed_chip[sec])
        else:
            keep = [c for c in passed_chip[sec] if news.get(c, ("",))[0] == "無明顯利空"]
        final[sec] = keep[:MAX_MAIN if sec == "主" else MAX_LOTTERY]
        funnel[sec].append(len(keep))

    track_lines = []
    for idx, r in active.iterrows():
        c = r["code"]
        if c not in ind["C"].columns:
            continue
        xn = EXIT_MA.get(r["section"], EXIT_MA["主"])
        xm = exit_ma.get(r["section"], exit_ma["主"])
        after = ind["C"].index[ind["C"].index > pd.Timestamp(r["listed"])]
        hit = None
        for t in after:
            if L.at[t, c] <= r["stop"]:
                hit = (t, f"觸及停損 {fmt_px(r['stop'])}")
                break
            if ind["C"].at[t, c] < xm.at[t, c]:
                hit = (t, f"週收跌破 {xn} 週線（{fmt_px(xm.at[t, c])}）")
                break
        now = C.get(c, np.nan)
        chg = now / r["ref_close"] - 1 if pd.notna(now) else np.nan
        tag = f"{c} {r['name']}（{r['listed'][5:]} 列入，參考價 {fmt_px(r['ref_close'])}）"
        if hit:
            track.loc[idx, ["status", "closed", "note"]] = ["已出場", hit[0].strftime("%Y-%m-%d"), hit[1]]
            track_lines.append(f"- ⚠️ **{tag}：{hit[1]} → 出場提醒**（列入至今 {chg:+.1%}）")
        else:
            track_lines.append(f"- {tag}：目前 {chg:+.1%}，續抱觀察"
                               f"（停損 {fmt_px(r['stop'])}／{xn} 週線 {fmt_px(xm.iloc[-1][c])}）")

    listed = wk.strftime("%Y-%m-%d")
    new_rows = [{"listed": listed, "code": c, "name": names.get(c, c), "section": sec,
                 "ref_close": round(float(C[c]), 2),
                 "stop": round(float(C[c] - bt.INIT_STOP_ATR * atr[c]), 2),
                 "status": "觀察中", "closed": "", "note": ""}
                for sec, codes in final.items() for c in codes if c not in set(active["code"])]
    if new_rows:
        track = pd.concat([track, pd.DataFrame(new_rows)], ignore_index=True)
    TRACK.parent.mkdir(exist_ok=True)
    track.to_csv(TRACK, index=False, encoding="utf-8")

    def stock_line(c: str, sec: str) -> list[str]:
        stop = C[c] - bt.INIT_STOP_ATR * atr[c]
        pct, chg = chips[c]
        extra_tag = "（ETF 成分股，超出回測範圍）" if c in extra else ""
        return [f"- **{c} {names.get(c, c)}**{extra_tag}｜週收 {fmt_px(C[c])}｜"
                f"400 張大戶 {pct:.1f}%（{chg:+.2f}）",
                f"  規則停損 {fmt_px(stop)}（{stop / C[c] - 1:+.0%}）｜出場：週收跌破 {EXIT_MA[sec]} 週線"
                f"（目前 {fmt_px(exit_ma[sec].iloc[-1][c])}）",
                f"  新聞：{news.get(c, ('', ''))[1] if use_ai else '（新聞關未啟用，請自行查看）'}"]

    out = ["## 🎯 本週選股觀察（週線長線・規則掃描，非買進建議）", "",
           f"_資料：{wk.date()} 當週週K（日線到 {last_day.date()}）；大戶資料週 {chip_week or '（無）'}_", "",
           "**【主｜52 週新高動能】**", *scorecard_lines(), ""]
    sections = [("主", None)]
    if MAX_LOTTERY:
        sections.append(("樂透", "**【樂透區｜趨勢轉強・統計上無優勢，小錢試試手氣】**"))
    for sec, title in sections:
        if title:
            out += [title, ""]
        t, ch, nw = (funnel[sec] + [0, 0, 0])[:3]
        out.append(f"_篩選：技術面 {t} 檔 → 大戶增加>{CHIP_MIN} {ch} 檔 → "
                   + (f"新聞無利空 {nw} 檔_" if use_ai else "新聞關未啟用_"))
        if news_failed and passed_chip[sec]:
            out.append(f"- 新聞檢查失敗，依寧缺勿濫不列入（技術＋大戶通過："
                       f"{'、'.join(f'{c} {names.get(c, c)}' for c in passed_chip[sec])}）")
        elif final[sec]:
            for c in final[sec]:
                out += stock_line(c, sec)
        else:
            out.append("- 本週沒有三關全過的股票（寧缺勿濫）")
        out.append("")
    out += ["**【觀察名單追蹤】**", *(track_lines or ["- （目前沒有追蹤中的股票）"]), "",
            "_停損與出場線由程式依回測規則計算；名單僅為規則掃描結果，非投資建議。_"]

    ARCHIVE.mkdir(parents=True, exist_ok=True)
    f = ARCHIVE / f"{run_day.date()}_選股.md"
    f.write_text("\n".join(out) + "\n", encoding="utf-8")
    log(f"OK 選股完成：主 {len(final['主'])} 檔、樂透 {len(final.get('樂透', []))} 檔、"
        f"追蹤 {len(track_lines)} 檔 → {f.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
