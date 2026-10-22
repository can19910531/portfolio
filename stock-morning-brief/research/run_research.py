"""
run_research.py — 依「pre_registration.md 修訂一」跑週線 21 組參數

挑選與驗收規則全部照登記，寫死在本檔，不看結果調整：
    - 樣本內（訊號 2016-01-01～2022-12-31）交易數 ≥ 150 筆中，挑平均超額最高的一組
    - 樣本外（訊號 2023-01-01 起）只驗收一次，用 回測.verdict() 判定
    - 另報告：樣本內前 5 名的樣本外表現、使用者假設 H1 開關對照、樂透區成績

產出：research/results_weekly.md、research/results_weekly.json

用法：
    python research/run_research.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import numpy as np          # noqa: E402
import pandas as pd         # noqa: E402

import backtest as bt         # noqa: E402

IS_START, IS_END, OOS_START = "2016-01-01", "2022-12-31", "2023-01-01"
MIN_IS_TRADES = 150
TRIGGERS = (13, 26, 52)
EXITS = ("ma10", "ma20", "chandelier")
EXIT_ZH = {"ma10": "跌破10週線", "ma20": "跌破20週線", "chandelier": "週吊燈停損"}


def label(trig, h1, ex) -> str:
    t = f"創{trig}週新高" if trig else "不要求突破"
    return f"{t}｜H1{'開' if h1 else '關'}｜{EXIT_ZH[ex]}"


def split(tr: pd.DataFrame):
    sd = tr["sig_date"]
    return (tr[(sd >= IS_START) & (sd <= IS_END)], tr[sd >= OOS_START])


def pct(x, signed=True):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x:+.2%}" if signed else f"{x:.1%}"


def row(s: dict) -> str:
    if not s.get("n"):
        return "0 | — | — | — | — |"
    return (f"{s['n']} | {pct(s['win'], False)} | {pct(s['exp'])} | {pct(s['excess'])} | "
            f"[{pct(s['ci_lo'])}, {pct(s['ci_hi'])}] |")


def load_0050() -> pd.Series:
    """0050 還原週收盤（事後補充比較用）"""
    import yfinance as yf
    px = yf.download("0050.TW", start="2015-01-01", auto_adjust=True, progress=False)["Close"]
    return px.squeeze().resample("W-FRI").last().dropna()


def hold_stats(px: pd.Series, start, end) -> dict:
    px = px[(px.index >= start) & (px.index <= end)]
    return bt.equity_stats(px / px.iloc[0]) if len(px) > 1 else {}


def port_block(tr: pd.DataFrame, ind: dict, px0050: pd.Series) -> dict:
    """投資組合模擬（最多同時 10 檔）：整段跑一條淨值曲線，再依日期切成樣本內／外兩段
    （各自分開跑的話，跨期間的部位會讓尾巴空手、稀釋報酬），並對齊同期間的 0050"""
    tr = tr[tr["sig_date"] >= IS_START]
    eq = bt.portfolio(tr, ind, slots=10)
    out = {}
    for k, seg in (("IS", eq[eq.index <= IS_END]), ("OOS", eq[eq.index >= OOS_START])):
        seg = seg / seg.iloc[0]
        out[k] = {"strat": bt.equity_stats(seg),
                  "hold": hold_stats(px0050, seg.index[0], seg.index[-1]),
                  "span": f"{seg.index[0].date()}～{seg.index[-1].date()}"}
    return out


def hold_block(tr: pd.DataFrame, px0050: pd.Series) -> dict:
    """每筆交易 vs 同期間改抱 0050"""
    h = bt.vs_hold(tr, px0050)
    out = {}
    for k, t in zip(("IS", "OOS"), split(h)):
        lo, hi = bt.cluster_bootstrap(t, col="vs_hold")
        out[k] = {"n": len(t), "mean": float(t["vs_hold"].mean()),
                  "beat": float((t["vs_hold"] > 0).mean()),
                  "hold": float(t["hold_ret"].mean()), "lo": float(lo), "hi": float(hi)}
    return out


def main():
    m = bt.load_market(start="2015-01-01")
    w = bt.to_weekly(m)
    ind = bt.indicators(w, **bt.WEEKLY)
    print(f"週 K：{len(w['C'])} 週 × {w['C'].shape[1]} 檔（{w['C'].index[0].date()} ～ "
          f"{w['C'].index[-1].date()}）", flush=True)

    grid = [(t, h, e) for t in TRIGGERS for h in (True, False) for e in EXITS]
    grid += [(None, True, e) for e in EXITS]
    assert len(grid) == 21, "組數必須與事先登記一致"

    results, trades = [], {}
    for trig, h1, ex in grid:
        tr = bt.simulate(bt.weekly_signal(ind, trig, h1), ind, ex)
        is_, oos = split(tr)
        key = label(trig, h1, ex)
        results.append({"key": key, "trig": trig, "h1": h1, "exit": ex,
                        "IS": bt.summarize(is_), "OOS": bt.summarize(oos)})
        trades[key] = tr
        print(f"  {key}：樣本內 {results[-1]['IS'].get('n', 0)} 筆、"
              f"超額 {pct(results[-1]['IS'].get('excess'))}", flush=True)

    eligible = [r for r in results if r["IS"].get("n", 0) >= MIN_IS_TRADES]
    if not eligible:
        print("X 沒有任何組合在樣本內達到 150 筆交易，無法挑選")
        sys.exit(1)
    ranked = sorted(eligible, key=lambda r: -r["IS"]["excess"])
    best = ranked[0]
    best_tr = trades[best["key"]]
    b_is, b_oos = split(best_tr)
    v = bt.verdict(best["OOS"])

    # 使用者指定 H1 必須包含 → 同突破、同出場、H1 開的版本（事前即已知的使用者限制）
    user = next(r for r in results
                if r["trig"] == best["trig"] and r["h1"] and r["exit"] == best["exit"])
    px0050 = load_0050()
    port = {"best": port_block(best_tr, ind, px0050),
            "user": port_block(trades[user["key"]], ind, px0050)}
    vs0050 = {"best": hold_block(best_tr, px0050),
              "user": hold_block(trades[user["key"]], px0050)}

    lot_tr = bt.simulate(bt.lottery_weekly(ind), ind, "ma10")
    l_is, l_oos = split(lot_tr)
    lottery = {"IS": bt.summarize(l_is), "OOS": bt.summarize(l_oos)}

    # H1 對照：同一組「突破 × 出場」，H1 開 vs 關
    h1_rows = []
    for t in TRIGGERS:
        for e in EXITS:
            on = next(r for r in results if r["trig"] == t and r["h1"] and r["exit"] == e)
            off = next(r for r in results if r["trig"] == t and not r["h1"] and r["exit"] == e)
            h1_rows.append((t, e, on, off))

    # ── 寫報告 ──
    L = [f"# 週線選股研究結果",
         "",
         f"> 執行時間：{datetime.now():%Y-%m-%d %H:%M}｜規則見 `pre_registration.md`（修訂一）",
         f"> 資料：{w['C'].index[0].date()} ～ {w['C'].index[-1].date()}，"
         f"{w['C'].shape[1]} 檔；樣本內＝訊號 {IS_START}～{IS_END}，樣本外＝{OOS_START} 起",
         "",
         f"## 結論：{v}",
         "",
         f"樣本內挑出的最佳組合：**{best['key']}**",
         "",
         "| 期間 | 筆數 | 勝率 | 每筆期望 | 平均超額 | 超額 95% 信賴區間 |",
         "|---|---|---|---|---|---|",
         f"| 樣本內 | {row(best['IS'])}",
         f"| **樣本外** | {row(best['OOS'])}",
         "",
         f"平均持有 {best['OOS'].get('days', 0):.0f} 週（樣本外）。"
         f"使用者指定須含 H1 的版本：**{user['key']}**，樣本外判定 {bt.verdict(user['OOS'])}。",
         "",
         "## ⚠️ 事後補充：跟「直接抱 0050」比（登記時沒想到，不影響上面的判定）",
         "",
         "登記的對照組是「同期前 200 大等權平均」，衡量的是挑股能力；但使用者實際的替代方案是",
         "**直接抱 0050**（市值加權、台積電佔比高）。兩者在台積電領漲的年份差距很大。",
         "",
         "### 每筆交易 vs 同一段期間改抱 0050",
         "",
         "| 版本 | 期間 | 筆數 | 同期抱 0050 平均 | 交易減 0050 | 贏過 0050 的比例 | 95% 信賴區間 |",
         "|---|---|---|---|---|---|---|"]
    for vk, vz in (("best", "最佳"), ("user", "含 H1")):
        for k, zh in (("IS", "樣本內"), ("OOS", "樣本外")):
            h = vs0050[vk][k]
            L.append(f"| {vz} | {zh} | {h['n']} | {pct(h['hold'])} | {pct(h['mean'])} | "
                     f"{pct(h['beat'], False)} | [{pct(h['lo'])}, {pct(h['hi'])}] |")
    L += ["", "### 投資組合模擬（最多同時 10 檔、每檔 1/10 淨值、訊號多時依相對強弱排序）", "",
          "| 版本 | 期間 | 模擬區間 | 策略年化 | 策略最大回撤 | 同期 0050 年化 | 0050 最大回撤 |",
          "|---|---|---|---|---|---|---|"]
    for vk, vz in (("best", "最佳"), ("user", "含 H1")):
        for k, zh in (("IS", "樣本內"), ("OOS", "樣本外")):
            p = port[vk][k]
            L.append(f"| {vz} | {zh} | {p['span']} | {pct(p['strat'].get('cagr'))} | "
                     f"{pct(p['strat'].get('mdd'))} | {pct(p['hold'].get('cagr'))} | "
                     f"{pct(p['hold'].get('mdd'))} |")
    yr = bt.by_year(best_tr)
    L += ["", "### 最佳組合分年度（依訊號年份）", "",
          "| 年份 | 筆數 | 勝率 | 每筆期望 | 超額 |", "|---|---|---|---|---|"]
    L += [f"| {y} | {int(r['筆數'])} | {pct(r['勝率'], False)} | {pct(r['每筆期望'])} | "
          f"{pct(r['超額'])} |" for y, r in yr.iterrows()]
    L += ["",
          f"獲利集中度：前 10 檔股票佔總報酬 {pct(best['OOS'].get('top10_share'), False)}（樣本外）",
          "", "## 樣本內前 5 名在樣本外的表現（看排名穩不穩定）", "",
          "| 樣本內名次 | 組合 | 樣本內超額 | 樣本外筆數 | 樣本外勝率 | 樣本外每筆 | 樣本外超額 | 樣本外 95% 區間 |",
          "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(ranked[:5], 1):
        L.append(f"| {i} | {r['key']} | {pct(r['IS']['excess'])} | {row(r['OOS'])}")
    L += ["", "## 使用者假設 H1：「加上 5 週線＋MACD 0 軸上＋KD≥70，勝率比較高」", "",
          "| 突破 | 出場 | 期間 | H1 開：筆數 / 勝率 / 超額 | H1 關：筆數 / 勝率 / 超額 |",
          "|---|---|---|---|---|"]
    for t, e, on, off in h1_rows:
        for k, zh in (("IS", "樣本內"), ("OOS", "樣本外")):
            a, b = on[k], off[k]
            fa = f"{a.get('n', 0)} / {pct(a.get('win'), False)} / {pct(a.get('excess'))}"
            fb = f"{b.get('n', 0)} / {pct(b.get('win'), False)} / {pct(b.get('excess'))}"
            L.append(f"| {t}週 | {EXIT_ZH[e]} | {zh} | {fa} | {fb} |")
    L += ["", "## 樂透區（週線趨勢轉強，不優化，跌破 10 週線出場）", "",
          "| 期間 | 筆數 | 勝率 | 每筆期望 | 平均超額 | 超額 95% 信賴區間 |",
          "|---|---|---|---|---|---|",
          f"| 樣本內 | {row(lottery['IS'])}", f"| 樣本外 | {row(lottery['OOS'])}",
          "", "## 全部 21 組（依樣本內超額排序）", "",
          "| 組合 | 樣本內：筆數 / 勝率 / 每筆 / 超額 / 區間 |", "|---|---|"]
    for r in sorted(results, key=lambda r: -(r["IS"].get("excess") or -9)):
        L.append(f"| {r['key']} | {row(r['IS']).rstrip(' |').replace(' | ', ' / ')} |")
    L += ["", "## 已知限制", "",
          "- 歷史市值用現行發行股數近似；已下市／被合併公司不在名單中，仍殘留部分倖存者偏差",
          "- 停損觸發若遇跌停假設能成交（偏樂觀）；週線以週最低價檢查停損，假設掛觸價單",
          "- 21 組裡挑最好的一組，**樣本內數字一定偏好看，只有樣本外可以當真**",
          "- 400 張大戶、新聞兩關不在本回測內（見事先登記）"]

    (HERE / "results_weekly.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    (HERE / "results_weekly.json").write_text(json.dumps(
        {"run_at": datetime.now().isoformat(timespec="seconds"), "verdict": v,
         "best": best, "user": user, "portfolio": port, "vs0050": vs0050,
         "lottery": lottery, "all": results},
        ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n結論：{v}\n最佳：{best['key']}\n樣本外：{bt.fmt(best['OOS'], '週')}")
    for vk in ("best", "user"):
        for k in ("IS", "OOS"):
            h, p = vs0050[vk][k], port[vk][k]
            print(f"  [{vk}/{k}] 每筆減0050 {pct(h['mean'])} [{pct(h['lo'])},{pct(h['hi'])}]"
                  f"｜組合年化 {pct(p['strat'].get('cagr'))} 回撤 {pct(p['strat'].get('mdd'))}"
                  f"｜0050 年化 {pct(p['hold'].get('cagr'))} 回撤 {pct(p['hold'].get('mdd'))}")
    print(f"報告：{HERE / 'results_weekly.md'}")


if __name__ == "__main__":
    main()
