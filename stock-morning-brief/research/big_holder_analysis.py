"""
big_holder_analysis.py — 依「pre_registration.md 修訂二」檢查 400 張大戶持股增加這一關有沒有用、門檻要不要提高

比較方式與決策規則全部照登記寫死，不看結果調整：
    測試 A：實際上線的主策略（創52週新高｜H1開｜跌破20週線）交易，依訊號週 Δ 分組
    測試 B：每週所有前 200 大，進場後 4／13 週超額，依 Δ 分四組（≤0／0~0.5／0.5~1／>1）
    決策：主看 B 的 13 週；提高門檻須 B 差值信賴區間下限 > 0 且 A 方向一致

產出：research/results_big_holders.md、research/results_big_holders.json

用法：
    python research/big_holder_analysis.py
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

STORE = HERE.parent / "data" / "big_holders.csv"
BINS = [(-np.inf, 0.0, "Δ≤0"), (0.0, 0.5, "0<Δ≤0.5"), (0.5, 1.0, "0.5<Δ≤1"), (1.0, np.inf, "Δ>1")]
HORIZONS = (4, 13)
CANDIDATES = (0.5, 1.0)
B = 2000


def pct(x, signed=True):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x:+.2%}" if signed else f"{x:.1%}"


def pp(x):
    return "—" if x is None or np.isnan(x) else f"{x:+.2f}"


# ── 大戶 Δ 表（列＝週五標籤、欄＝代號）──────────────────────────────────
def load_delta() -> pd.DataFrame:
    s = pd.read_csv(STORE, dtype={"date": str, "code": str})
    s["pct_400"] = pd.to_numeric(s["pct_400"], errors="coerce")
    s["week"] = pd.to_datetime(s["date"]).dt.to_period("W-FRI").dt.end_time.dt.normalize()
    s = s.sort_values("date")
    pv = s.pivot_table(index="week", columns="code", values="pct_400", aggfunc="last").sort_index()
    d = pv.diff()
    gap = pv.index.to_series().diff().dt.days
    d.loc[gap.ne(7).to_numpy()] = np.nan          # 上一週缺資料就不算 Δ
    return d


def bucket(x: float) -> str | None:
    for lo, hi, name in BINS:
        if lo < x <= hi:
            return name
    return None


# ── 統計 ──────────────────────────────────────────────────────────────
def group_stats(df: pd.DataFrame, ret: str, ex: str) -> dict:
    if df.empty:
        return {"n": 0}
    return {"n": int(len(df)), "win": float((df[ret] > 0).mean()),
            "ret": float(df[ret].mean()), "excess": float(df[ex].mean()),
            "beat": float((df[ex] > 0).mean())}


def diff_boot(df: pd.DataFrame, a_mask, b_mask, col: str, cluster: str, seed: int = 0):
    """兩組平均之差（a − b）與以 cluster 為單位的自助法 95% 信賴區間"""
    a, b = df[a_mask], df[b_mask]
    if len(a) < 5 or len(b) < 5:
        return (np.nan, np.nan, np.nan)
    keys = pd.Index(sorted(df.loc[a_mask | b_mask, cluster].unique()))
    sa = a.groupby(cluster)[col].agg(["sum", "count"]).reindex(keys, fill_value=0)
    sb = b.groupby(cluster)[col].agg(["sum", "count"]).reindex(keys, fill_value=0)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(keys), size=(B, len(keys)))
    with np.errstate(all="ignore"):
        ma = sa["sum"].to_numpy()[picks].sum(1) / sa["count"].to_numpy()[picks].sum(1)
        mb = sb["sum"].to_numpy()[picks].sum(1) / sb["count"].to_numpy()[picks].sum(1)
    diffs = (ma - mb)[~np.isnan(ma - mb)]
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return (float(a[col].mean() - b[col].mean()), float(lo), float(hi))


# ── 測試 B：每週全部前 200 大的前瞻報酬 ────────────────────────────────
def forward_panel(ind: dict, delta: pd.DataFrame, tech: pd.DataFrame) -> pd.DataFrame:
    O, C, top = ind["O"], ind["C"], ind["top"].fillna(False)
    dates = O.index
    cols = [c for c in O.columns if c in delta.columns]
    Ov, Cv = O[cols].to_numpy(), C[cols].to_numpy()
    T = top[cols].to_numpy()
    TECH = tech.reindex(index=dates, columns=cols).fillna(False).to_numpy()
    Ofull, Tfull = O.to_numpy(), top.to_numpy()
    D = delta.reindex(index=dates, columns=cols).to_numpy()
    rows = []
    for i in np.flatnonzero(~np.isnan(D).all(axis=1)):
        e = i + 1
        if e >= len(dates):
            continue
        rec = {}
        for h in HORIZONS:
            x = e + h
            if x >= len(dates):
                rec[h] = None
                continue
            with np.errstate(all="ignore"):
                bench = np.nanmean(Ofull[x, Tfull[i]] / Ofull[e, Tfull[i]] - 1) - bt.COST
                rec[h] = (Ov[x] / Ov[e] - 1 - bt.COST, bench)
        for k in np.flatnonzero(T[i] & ~np.isnan(D[i])):
            if np.isnan(Ov[e, k]) or Ov[e, k] >= Cv[i, k] * bt.LIMIT_UP_OPEN:
                continue
            r = {"week": dates[i], "code": cols[k], "delta": D[i, k], "tech": bool(TECH[i, k])}
            for h in HORIZONS:
                if rec[h] is None or np.isnan(rec[h][0][k]):
                    r[f"ret{h}"] = r[f"ex{h}"] = np.nan
                else:
                    r[f"ret{h}"] = rec[h][0][k]
                    r[f"ex{h}"] = rec[h][0][k] - rec[h][1]
            rows.append(r)
    df = pd.DataFrame(rows)
    df["grp"] = df["delta"].map(bucket)
    return df


def comparisons(df, ex, cluster):
    """決策規則要的三組比較：Δ>0 vs Δ≤0、Δ>T vs 0<Δ≤T（T＝0.5、1）"""
    d = df["delta"]
    out = {"gate": diff_boot(df, d > 0, d <= 0, ex, cluster)}
    for t in CANDIDATES:
        out[f">{t}"] = diff_boot(df, d > t, (d > 0) & (d <= t), ex, cluster)
    return out


def a_split(tr: pd.DataFrame, t: float) -> tuple[float, float]:
    """測試 A：Δ>T 與 0<Δ≤T 兩組交易的平均超額"""
    hi = tr.loc[tr["delta"] > t, "excess"]
    mid = tr.loc[(tr["delta"] > 0) & (tr["delta"] <= t), "excess"]
    return (float(hi.mean()) if len(hi) else np.nan, float(mid.mean()) if len(mid) else np.nan)


def main():
    delta = load_delta()
    weeks = delta.dropna(how="all").index
    print(f"大戶 Δ：{len(weeks)} 週（{weeks[0].date()} ～ {weeks[-1].date()}）× {delta.shape[1]} 檔",
          flush=True)

    m = bt.load_market(start="2015-01-01", max_age_hours=20)
    w = bt.to_weekly(m)
    ind = bt.indicators(w, **bt.WEEKLY)
    print(f"週 K 到 {w['C'].index[-1].date()}", flush=True)

    # 測試 A
    sig = bt.weekly_signal(ind, 52, True)
    tr = bt.simulate(sig, ind, "ma20")
    dl = delta.stack().rename("delta")
    tr = tr.join(dl, on=["sig_date", "code"]).dropna(subset=["delta"]).copy()
    tr["grp"] = tr["delta"].map(bucket)
    tr["month"] = tr["entry_date"].dt.to_period("M")
    lot = bt.simulate(bt.lottery_weekly(ind), ind, "ma10")
    lot = lot.join(dl, on=["sig_date", "code"]).dropna(subset=["delta"]).copy()
    lot["month"] = lot["entry_date"].dt.to_period("M")

    A = {"inc": group_stats(tr[tr["delta"] > 0], "ret", "excess"),
         "flat": group_stats(tr[tr["delta"] <= 0], "ret", "excess"),
         "by_grp": {g: group_stats(tr[tr["grp"] == g], "ret", "excess") for *_, g in BINS},
         "cmp": comparisons(tr, "excess", "month"),
         "open": int(tr["still_open"].sum()),
         "lot_inc": group_stats(lot[lot["delta"] > 0], "ret", "excess"),
         "lot_flat": group_stats(lot[lot["delta"] <= 0], "ret", "excess")}

    # 測試 B
    panel = forward_panel(ind, delta, sig)
    Bres = {}
    for h in HORIZONS:
        p = panel.dropna(subset=[f"ex{h}"])
        Bres[h] = {"weeks": int(p["week"].nunique()),
                   "by_grp": {g: group_stats(p[p["grp"] == g], f"ret{h}", f"ex{h}") for *_, g in BINS},
                   "cmp": comparisons(p, f"ex{h}", "week"),
                   "tech_inc": group_stats(p[p["tech"] & (p["delta"] > 0)], f"ret{h}", f"ex{h}"),
                   "tech_flat": group_stats(p[p["tech"] & (p["delta"] <= 0)], f"ret{h}", f"ex{h}")}

    # 每週平均有幾檔過各門檻（技術面已過關的那些）
    tp = panel[panel["tech"]]
    per_week = {f">{t}": float((tp["delta"] > t).groupby(tp["week"]).sum().mean()) for t in (0,) + CANDIDATES}
    per_week["tech"] = float(tp.groupby("week").size().mean()) if len(tp) else 0.0

    # ── 決策（照修訂二）──
    b13 = Bres[13]["cmp"]
    passed = []
    for t in CANDIDATES:
        d, lo, _ = b13[f">{t}"]
        a_hi, a_mid = a_split(tr, t)
        ok_b = not np.isnan(lo) and lo > 0 and d > 0
        ok_a = not (np.isnan(a_hi) or np.isnan(a_mid)) and a_hi > a_mid
        passed.append((t, ok_b, ok_a, a_hi, a_mid))
    winners = [t for t, ob, oa, *_ in passed if ob and oa]
    threshold = min(winners) if winners else 0.0
    g_d, g_lo, g_hi = b13["gate"]
    gate_bad = not np.isnan(g_hi) and g_hi < 0

    # ── 報告 ──
    def grp_row(s):
        if not s.get("n"):
            return "0 | — | — | — | — |"
        return (f"{s['n']} | {pct(s['win'], False)} | {pct(s['ret'])} | {pct(s['excess'])} | "
                f"{pct(s['beat'], False)} |")

    def cmp_row(name, c):
        return f"| {name} | {pct(c[0])} | [{pct(c[1])}, {pct(c[2])}] |"

    L = ["# 400 張大戶增加：初步分析", "",
         f"> 執行時間：{datetime.now():%Y-%m-%d %H:%M}｜規則見 `pre_registration.md`（修訂二）",
         f"> 大戶資料：{weeks[0].date()} ～ {weeks[-1].date()}（{len(weeks)} 週有 Δ）；"
         f"週 K 到 {w['C'].index[-1].date()}",
         "> ⚠️ **初步參考**：只有約一年資料，且與先前回測的樣本外期間重疊，不能當證據。", "",
         "## 結論", "",
         f"- 門檻：**{'維持 >0' if threshold == 0 else f'提高到 >{threshold}'}**",
         f"- 大戶關本身：{'⚠️ 增加的那組反而顯著較差，需跟使用者討論是否拿掉' if gate_bad else '照使用者要求保留（仍標「未經證實」）'}",
         "", "### 門檻候選逐項檢查（測試 B 13 週為主、測試 A 看方向）", "",
         "| 門檻 | B：Δ>T 減 0<Δ≤T | B 95% 區間 | B 過關 | A：Δ>T 交易超額 | A：0<Δ≤T 交易超額 | A 方向一致 |",
         "|---|---|---|---|---|---|---|"]
    for t, ob, oa, a_hi, a_mid in passed:
        d, lo, hi = b13[f">{t}"]
        L.append(f"| >{t} | {pct(d)} | [{pct(lo)}, {pct(hi)}] | {'✅' if ob else '❌'} | "
                 f"{pct(a_hi)} | {pct(a_mid)} | {'✅' if oa else '❌'} |")
    L += ["", f"大戶關整體（B 13 週，Δ>0 減 Δ≤0）：{pct(g_d)}，95% 區間 [{pct(g_lo)}, {pct(g_hi)}]", "",
          "## 測試 B：每週全部前 200 大（不限策略）", ""]
    for h in HORIZONS:
        r = Bres[h]
        L += [f"### 進場後 {h} 週（{r['weeks']} 個訊號週）", "",
              "| Δ 分組（百分點） | 筆數 | 勝率 | 平均報酬 | 平均超額 | 贏過大型股平均的比例 |",
              "|---|---|---|---|---|---|"]
        L += [f"| {g} | {grp_row(r['by_grp'][g])}" for *_, g in BINS]
        L += ["", "| 比較 | 超額差 | 95% 區間（以週為群集） |", "|---|---|---|",
              cmp_row("Δ>0 減 Δ≤0", r["cmp"]["gate"])]
        L += [cmp_row(f"Δ>{t} 減 0<Δ≤{t}", r["cmp"][f">{t}"]) for t in CANDIDATES]
        L += ["", "技術面已過關（創52週新高＋H1）的子集合：", "",
              "| 分組 | 筆數 | 勝率 | 平均報酬 | 平均超額 | 贏過大型股平均的比例 |",
              "|---|---|---|---|---|---|",
              f"| 大戶增加 | {grp_row(r['tech_inc'])}", f"| 大戶沒增加 | {grp_row(r['tech_flat'])}", ""]
    L += ["## 測試 A：實際上線的主策略交易（創52週新高｜H1開｜跌破20週線）", "",
          f"含未出場 {A['open']} 筆（以最後收盤價計）。", "",
          "| 分組 | 筆數 | 勝率 | 每筆報酬 | 平均超額 | 贏過大型股平均的比例 |",
          "|---|---|---|---|---|---|",
          f"| **大戶增加（Δ>0）** | {grp_row(A['inc'])}", f"| 大戶沒增加（Δ≤0） | {grp_row(A['flat'])}"]
    L += [f"| {g} | {grp_row(A['by_grp'][g])}" for *_, g in BINS]
    L += ["", "| 比較 | 超額差 | 95% 區間（以進場月為群集） |", "|---|---|---|",
          cmp_row("Δ>0 減 Δ≤0", A["cmp"]["gate"])]
    L += [cmp_row(f"Δ>{t} 減 0<Δ≤{t}", A["cmp"][f">{t}"]) for t in CANDIDATES]
    L += ["", "### 樂透區（只報告）", "",
          "| 分組 | 筆數 | 勝率 | 每筆報酬 | 平均超額 | 贏過大型股平均的比例 |",
          "|---|---|---|---|---|---|",
          f"| 大戶增加 | {grp_row(A['lot_inc'])}", f"| 大戶沒增加 | {grp_row(A['lot_flat'])}",
          "", "## 門檻對候選數的影響（技術面已過關的股票，每週平均）", "",
          f"技術面過關 {per_week['tech']:.1f} 檔 → 大戶 Δ>0 剩 {per_week['>0']:.1f} 檔、"
          f"Δ>0.5 剩 {per_week['>0.5']:.1f} 檔、Δ>1 剩 {per_week['>1.0']:.1f} 檔", "",
          "## 已知限制", "",
          "- 只有約一年、且與先前回測的樣本外期間重疊；這一年台股大漲，結果可能只反映這段行情",
          "- 測試 B 的觀察值每週重疊（同一檔連續好幾週都算一次），已用「以週為群集」的區間處理，但仍偏樂觀",
          "- 13 週前瞻需要訊號後 13 週的資料，最近三個月的訊號沒有算進去",
          "- 預定 2027-11 資料累積到兩年時，用同一支程式重跑"]
    (HERE / "results_big_holders.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    (HERE / "results_big_holders.json").write_text(json.dumps(
        {"run_at": datetime.now().isoformat(timespec="seconds"), "threshold": threshold,
         "gate_bad": gate_bad, "A": A, "B": Bres, "candidates": passed, "per_week": per_week},
        ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("\n".join(L[:20]))
    print(f"\n報告：{HERE / '結果_大戶.md'}")


if __name__ == "__main__":
    main()
