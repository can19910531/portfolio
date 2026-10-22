"""
backtest.py — 選股策略回測引擎

三個「不騙自己」的設計：
    1. 去除倖存者偏差：每個交易日用「當天」的市值重算前 200 大，訊號只在當時就是
       大型股的股票上才算數（不能用今天的贏家名單回測過去）
    2. 扣交易成本：手續費 0.1425%×2＋證交稅 0.3%；隔天開盤近漲停視為買不到
    3. 跟「同一天的前 200 大平均」比：超額報酬＝交易報酬−基準，扣掉大盤漲跌，
       只衡量「挑股」本身有沒有本事；統計檢定以進場月份做群集自助法

給 research/run_research.py 與 screener.py 共用。

用法：
    python backtest.py              # 用目前的預設規則跑一次報告（全期間）
    python backtest.py --refresh       # 強制重新下載行情
"""
import argparse
import json
import pickle
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
CACHE = HERE / "backtest_cache"

TOP_N = 200
FEE = 0.001425
TAX = 0.003
COST = FEE * 2 + TAX            # 來回成本
LIMIT_UP_OPEN = 1.095           # 隔天開盤 ≥ 前收×1.095 視為漲停買不到
INIT_STOP_ATR = 2.0             # 初始停損：訊號日收盤 − 2×ATR
CHANDELIER_ATR = 3.0            # 吊燈停損：持有期最高收盤 − 3×ATR

TWSE_INFO = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_INFO = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"


# ── 資料 ──────────────────────────────────────────────────────────────
def _get_json(url: str, tries: int = 4):
    """官方開放資料：網路瞬斷（DNS、連線重置）時自動重試，間隔 10/20/40 秒"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if i == tries - 1:
                raise RuntimeError(f"[錯誤] 讀取官方資料失敗（網路不通或對方暫時掛掉？）：{url}｜{e}")
            print(f"  ! 官方資料讀取失敗（{type(e).__name__}），{10 * 2 ** i} 秒後重試…", flush=True)
            time.sleep(10 * 2 ** i)


def _num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except ValueError:
        return None


def fetch_meta() -> pd.DataFrame:
    """證交所＋櫃買中心官方現行名單：代號、簡稱、市場別、發行股數、Yahoo 代號"""
    rows = []
    for d in _get_json(TWSE_INFO):
        sh = _num(d.get("已發行普通股數或TDR原股發行股數"))
        if sh:
            rows.append((d["公司代號"].strip(), d["公司簡稱"].strip(), "上市", sh))
    for d in _get_json(TPEX_INFO):
        sh = _num(d.get("IssueShares"))
        if sh:
            rows.append((d["SecuritiesCompanyCode"].strip(),
                         d["CompanyAbbreviation"].strip(), "上櫃", sh))
    meta = pd.DataFrame(rows, columns=["code", "name", "board", "shares"])
    meta = meta.drop_duplicates("code").set_index("code")
    # ⚠️ 上櫃股在 Yahoo 是 .TWO，不是 .TW
    meta["ticker"] = [f"{c}.TWO" if b == "上櫃" else f"{c}.TW"
                      for c, b in zip(meta.index, meta["board"])]
    return meta


def _download(tickers: list, start: str) -> pd.DataFrame:
    import yfinance as yf
    frames = []
    for i in range(0, len(tickers), 300):
        part = tickers[i:i + 300]
        for attempt in range(3):
            try:
                frames.append(yf.download(part, start=start, interval="1d",
                                          group_by="ticker", auto_adjust=False,
                                          threads=True, progress=False))
                break
            except Exception as e:
                if attempt == 2:
                    raise RuntimeError(f"[錯誤] 行情下載失敗（第 {i} 檔起，網路或 Yahoo 限流？"
                                       f"稍等幾分鐘再跑）：{e}")
                time.sleep(15)
        print(f"  已下載 {min(i + 300, len(tickers))}/{len(tickers)} 檔", flush=True)
    return pd.concat(frames, axis=1)


def load_market(start: str = "2015-01-01", refresh: bool = False,
                max_age_hours: float = 20, codes: list | None = None, tag: str = "") -> dict:
    """日線（批次下載＋快取）。codes 給定時只抓那些股票（每週掃描用），否則抓全市場。
       回傳：meta、O/H/L/C（還原權息）、C_raw（未還原，算市值用）、V"""
    CACHE.mkdir(exist_ok=True)
    f = CACHE / f"market_{start}{'_' + tag if tag else ''}.pkl"
    if f.exists() and not refresh:
        age_h = (time.time() - f.stat().st_mtime) / 3600
        if age_h < max_age_hours:
            with f.open("rb") as fh:
                return pickle.load(fh)

    print("→ 抓官方名單…", flush=True)
    meta = fetch_meta()
    if codes is not None:
        meta = meta.loc[meta.index.intersection(list(codes))]
    print(f"→ 下載 {len(meta)} 檔日線（{start} 起）…", flush=True)
    raw = _download(list(meta["ticker"]), start)

    have = set(raw.columns.get_level_values(0))
    fields = {}
    for fld in ("Open", "High", "Low", "Close", "Adj Close", "Volume"):
        fields[fld] = pd.DataFrame({c: raw[t][fld] for c, t in meta["ticker"].items()
                                    if t in have})
    close = fields["Close"].replace(0, np.nan)
    keep = close.columns[close.notna().sum() > 0]
    close = close[keep].dropna(how="all").sort_index()
    idx = close.index
    adj = (fields["Adj Close"][keep].reindex(idx) / close).replace([np.inf, -np.inf], np.nan)

    m = {
        "meta": meta.loc[keep],
        "C_raw": close,
        "O": fields["Open"][keep].reindex(idx).replace(0, np.nan) * adj,
        "H": fields["High"][keep].reindex(idx).replace(0, np.nan) * adj,
        "L": fields["Low"][keep].reindex(idx).replace(0, np.nan) * adj,
        "C": close * adj,
        "V": fields["Volume"][keep].reindex(idx),
        "fetched": datetime.now().isoformat(timespec="seconds"),
    }
    with f.open("wb") as fh:
        pickle.dump(m, fh)
    print(f"OK 行情 {len(keep)} 檔 × {len(idx)} 日（{idx[0].date()} ～ {idx[-1].date()}）", flush=True)
    return m


def to_weekly(m: dict) -> dict:
    """日線合成週 K（週一開、週內高、週內低、週五收、量加總）；標籤為該週週五"""
    wk = {
        "O": m["O"].resample("W-FRI").first(),
        "H": m["H"].resample("W-FRI").max(),
        "L": m["L"].resample("W-FRI").min(),
        "C": m["C"].resample("W-FRI").last(),
        "C_raw": m["C_raw"].resample("W-FRI").last(),
        "V": m["V"].resample("W-FRI").sum(min_count=1),
    }
    keep = wk["C"].dropna(how="all").index
    out = {k: v.reindex(keep) for k, v in wk.items()}
    out["meta"] = m["meta"]
    return out


# ── 指標（全市場一次算，寬表：列＝日期、欄＝股票）─────────────────────────
DAILY = dict(ma=(5, 20, 60, 200), hh=(20, 60, 120), rs_n=120, v_n=20)
WEEKLY = dict(ma=(5, 10, 20, 40), hh=(13, 26, 52), rs_n=26, v_n=10)


def indicators(m: dict, ma=DAILY["ma"], hh=DAILY["hh"],
               rs_n: int = DAILY["rs_n"], v_n: int = DAILY["v_n"]) -> dict:
    """日線用預設參數；週線請傳 **WEEKLY"""
    C, H, L, O, V = m["C"], m["H"], m["L"], m["O"], m["V"]
    shares = m["meta"]["shares"].reindex(C.columns)
    mcap = m["C_raw"].mul(shares, axis=1)
    top = mcap.rank(axis=1, ascending=False, method="first") <= TOP_N   # 當期前 200 大

    prev = C.shift(1)
    tr = np.maximum(H - L, np.maximum((H - prev).abs(), (L - prev).abs()))
    ret_rs = C / C.shift(rs_n) - 1
    e12, e26 = C.ewm(span=12, adjust=False).mean(), C.ewm(span=26, adjust=False).mean()
    dif = e12 - e26
    # KD(9,3,3)：台灣慣用參數；K、D 以 1/3 權重遞迴平滑
    llv, hhv = L.rolling(9, min_periods=9).min(), H.rolling(9, min_periods=9).max()
    rsv = ((C - llv) / (hhv - llv) * 100).where(hhv > llv)
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    return {
        "O": O, "H": H, "L": L, "C": C, "V": V, "top": top,
        "ma": {n: C.rolling(n, min_periods=n).mean() for n in ma},
        "hh": {n: H.shift(1).rolling(n, min_periods=n).max() for n in hh},
        "vavg": V.shift(1).rolling(v_n, min_periods=v_n).mean(),
        "atr": tr.rolling(14, min_periods=14).mean(),
        "rs": ret_rs.where(top).rank(axis=1, pct=True),   # 相對強弱：在當期前 200 大中的百分位
        "dif": dif, "dea": dif.ewm(span=9, adjust=False).mean(),
        "k": k, "d": k.ewm(alpha=1 / 3, adjust=False).mean(),
    }


# ── 訊號 ──────────────────────────────────────────────────────────────
def momentum_signal(ind: dict, n: int = 20, trend: str = "A",
                    rs: bool = False, vol: bool = True) -> pd.DataFrame:
    """動能突破。trend A＝收盤>月線且月線>季線；B＝收盤>200日線"""
    C, ma = ind["C"], ind["ma"]
    sig = ind["top"] & (C > ind["hh"][n]) & ind["atr"].notna()
    sig &= ((C > ma[20]) & (ma[20] > ma[60])) if trend == "A" else (C > ma[200])
    if rs:
        sig &= ind["rs"] >= 0.7
    if vol:
        sig &= ind["V"] > 1.5 * ind["vavg"]
    return sig


def user_filter(ind: dict, kd_min: float = 70) -> pd.DataFrame:
    """使用者指定的強勢濾網 H1（2026-10-22）：收盤站上 5 期均線、MACD 快線在 0 軸上、K 值 ≥ 70
    傳週線指標就是「5 週線／週 MACD／週 KD」"""
    return (ind["C"] > ind["ma"][5]) & (ind["dif"] > 0) & (ind["k"] >= kd_min)


def weekly_signal(ind: dict, trigger: int | None = 26, h1: bool = True) -> pd.DataFrame:
    """週線選股（主策略）。長期趨勢濾網固定開：週收 > 40 週線
    trigger：創 N 週新高（13/26/52），None＝不要求突破；h1：使用者濾網"""
    C = ind["C"]
    sig = ind["top"] & ind["atr"].notna() & (C > ind["ma"][40])
    if trigger:
        sig &= C > ind["hh"][trigger]
    if h1:
        sig &= user_filter(ind)
    return sig


def lottery_weekly(ind: dict) -> pd.DataFrame:
    """樂透區（週線版，不優化）：2 週內 10 週線上穿 20 週線＋週 MACD 多方＋週收 > 10 週線"""
    ma = ind["ma"]
    gx = (ma[10] > ma[20]) & (ma[10].shift(1) <= ma[20].shift(1))
    gx2 = gx.astype(float).rolling(2, min_periods=1).max().astype(bool)
    return ind["top"] & gx2 & (ind["dif"] > ind["dea"]) & (ind["C"] > ma[10]) & ind["atr"].notna()


def lottery_signal(ind: dict) -> pd.DataFrame:
    """趨勢轉強（樂透區，不優化）：5 日內月線上穿季線＋MACD 多方＋收盤站上月線"""
    ma = ind["ma"]
    gx = (ma[20] > ma[60]) & (ma[20].shift(1) <= ma[60].shift(1))
    gx5 = gx.astype(float).rolling(5, min_periods=1).max().astype(bool)
    return ind["top"] & gx5 & (ind["dif"] > ind["dea"]) & (ind["C"] > ma[20]) & ind["atr"].notna()


# ── 交易模擬 ───────────────────────────────────────────────────────────
def simulate(sig: pd.DataFrame, ind: dict, exit_mode: str = "ma20") -> pd.DataFrame:
    """exit_mode：maN（收盤跌破 N 期均線→下一期開盤出，如 ma20、ma10）、
    chandelier（吊燈停損）。日線、週線皆可（傳對應頻率的 sig 與 ind）
    每筆都附「同期進場、同期出場」的前 200 大等權基準報酬"""
    dates = sig.index
    cols = sig.columns
    O, H, L, C = (ind[k].reindex(index=dates, columns=cols).to_numpy() for k in "OHLC")
    A = ind["atr"].reindex(index=dates, columns=cols).to_numpy()
    RS = ind["rs"].reindex(index=dates, columns=cols).to_numpy()
    TOP = ind["top"].reindex(index=dates, columns=cols).fillna(False).to_numpy()
    MA = (ind["ma"][int(exit_mode[2:])]
          .reindex(index=dates, columns=cols).to_numpy()) if exit_mode.startswith("ma") else None
    S = sig.reindex(index=dates, columns=cols).fillna(False).to_numpy()
    n = len(dates)
    out = []

    for k in range(len(cols)):
        free = 0
        for i in np.flatnonzero(S[:, k]):
            if i < free or i + 1 >= n:
                continue
            e = i + 1
            if np.isnan(O[e, k]) or np.isnan(C[i, k]) or O[e, k] >= C[i, k] * LIMIT_UP_OPEN:
                continue                                     # 停牌或漲停買不到
            entry = O[e, k]
            stop = C[i, k] - INIT_STOP_ATR * A[i, k]
            if not stop > 0:
                continue
            trail, hc, px, j, still = stop, C[i, k], None, e, False
            while j < n:
                if np.isnan(O[j, k]):                        # 停牌日跳過
                    j += 1
                    continue
                if L[j, k] <= trail:
                    px = min(O[j, k], trail)
                    break
                if MA is not None and C[j, k] < MA[j, k]:
                    j2 = j + 1
                    while j2 < n and np.isnan(O[j2, k]):
                        j2 += 1
                    if j2 < n:
                        j, px = j2, O[j2, k]
                        break
                if exit_mode == "chandelier" and not np.isnan(A[j, k]):
                    hc = max(hc, C[j, k])
                    trail = max(trail, hc - CHANDELIER_ATR * A[j, k])
                j += 1
            if px is None:                                   # 回測結束仍持有
                j = n - 1
                while j > e and np.isnan(C[j, k]):
                    j -= 1
                px, still = C[j, k], True
            mask = TOP[i]
            with np.errstate(all="ignore"):
                bench = np.nanmean(O[j, mask] / O[e, mask] - 1) - COST
            ret = px / entry - 1 - COST
            out.append((cols[k], dates[i], dates[e], dates[j], j - e + 1,
                        entry, px, ret, bench, ret - bench, RS[i, k], still))
            free = j + 1
    return pd.DataFrame(out, columns=["code", "sig_date", "entry_date", "exit_date", "days",
                                      "entry", "exit", "ret", "bench", "excess", "rs", "still_open"])


# ── 統計 ──────────────────────────────────────────────────────────────
def cluster_bootstrap(tr: pd.DataFrame, col: str = "excess", B: int = 2000, seed: int = 0):
    """以進場月份為群集的自助法 95% 信賴區間（同月進場的交易會一起漲跌，不能當獨立）"""
    if len(tr) < 20:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    g = tr.groupby(tr["entry_date"].dt.to_period("M"))[col]
    sums, cnts = g.sum().to_numpy(), g.count().to_numpy()
    k = len(sums)
    picks = rng.integers(0, k, size=(B, k))
    means = sums[picks].sum(axis=1) / cnts[picks].sum(axis=1)
    return tuple(np.percentile(means, [2.5, 97.5]))


def summarize(tr: pd.DataFrame) -> dict:
    if tr.empty:
        return {"n": 0}
    r = tr["ret"]
    w, l = r[r > 0], r[r <= 0]
    lo, hi = cluster_bootstrap(tr)
    by_code = tr.groupby("code")["ret"].sum().sort_values(ascending=False)
    total = r.sum()
    return {
        "n": int(len(r)),
        "win": float(len(w) / len(r)),
        "avg_win": float(w.mean()) if len(w) else 0.0,
        "avg_loss": float(l.mean()) if len(l) else 0.0,
        "pf": float(w.sum() / -l.sum()) if l.sum() < 0 else float("inf"),
        "exp": float(r.mean()),
        "median": float(r.median()),
        "bench": float(tr["bench"].mean()),
        "excess": float(tr["excess"].mean()),
        "ci_lo": float(lo), "ci_hi": float(hi),
        "days": float(tr["days"].mean()),
        "top10_share": float(by_code.head(10).sum() / total) if total > 0 else float("nan"),
    }


def by_year(tr: pd.DataFrame) -> pd.DataFrame:
    g = tr.groupby(tr["sig_date"].dt.year)
    return pd.DataFrame({"筆數": g.size(), "勝率": g["ret"].apply(lambda x: (x > 0).mean()),
                         "每筆期望": g["ret"].mean(), "超額": g["excess"].mean()})


def verdict(s: dict) -> str:
    """事先登記的過關標準（research/pre_registration.md）"""
    if s.get("n", 0) == 0 or not s["excess"] > 0:
        return "❌ 不通過"
    if s["ci_lo"] > 0 and s["exp"] > 0:
        return "✅ 通過"
    return "🟡 有苗頭（統計上尚未確立）"


def fmt(s: dict, unit: str = "天") -> str:
    """unit：持有期間的單位（日線＝天、週線＝週；summarize 的 days 欄位其實是「幾根 K 棒」）"""
    if not s.get("n"):
        return "（無交易）"
    return (f"{s['n']} 筆｜勝率 {s['win']:.1%}｜每筆期望 {s['exp']:+.2%}（中位數 {s['median']:+.2%}）｜"
            f"獲利因子 {s['pf']:.2f}｜超額 {s['excess']:+.2%} "
            f"[95% {s['ci_lo']:+.2%}, {s['ci_hi']:+.2%}]｜平均持有 {s['days']:.0f} {unit}")


def vs_hold(tr: pd.DataFrame, px: pd.Series, cost: float = FEE * 2 + 0.001) -> pd.DataFrame:
    """每筆交易 vs「同一段期間改抱某檔 ETF」（預設成本：手續費×2＋ETF 證交稅 0.1%）
    px：該 ETF 的還原收盤價（與交易同頻率）。以訊號當期收盤→出場當期收盤近似"""
    px = px.dropna()
    a = px.reindex(tr["sig_date"], method="ffill").to_numpy()
    b = px.reindex(tr["exit_date"], method="ffill").to_numpy()
    out = tr.copy()
    out["hold_ret"] = b / a - 1 - cost
    out["vs_hold"] = out["ret"] - out["hold_ret"]
    return out


# ── 投資組合模擬（給最終規則估真實回撤用）─────────────────────────────
def portfolio(tr: pd.DataFrame, ind: dict, slots: int = 10) -> pd.Series:
    """最多同時持有 slots 檔、每檔投入當時淨值的 1/slots；訊號多於空位時依相對強弱排序。
    只模擬到這批交易最後一筆出場為止（避免之後全放現金稀釋報酬）。回傳每期淨值（起始 1.0）"""
    C = ind["C"]
    dates = C.index
    pos = {d: i for i, d in enumerate(dates)}
    Cn = C.to_numpy()
    colpos = {c: i for i, c in enumerate(C.columns)}
    tr = tr.sort_values(["entry_date", "rs"], ascending=[True, False])
    by_entry = {d: g for d, g in tr.groupby("entry_date")}
    cash, held, eq = 1.0, [], []
    start = pos[tr["entry_date"].min()] if len(tr) else 0
    end = pos[tr["exit_date"].max()] + 1 if len(tr) else len(dates)
    for t in range(start, end):
        d = dates[t]
        still = []
        for h in held:                                    # 先處理今天出場的
            if h["x"] == t:
                cash += h["sh"] * h["px"] * (1 - FEE - TAX)
            else:
                still.append(h)
        held = still
        value = cash + sum(h["sh"] * h["last"] for h in held)
        if d in by_entry:
            for _, r in by_entry[d].iterrows():
                if len(held) >= slots:
                    break
                if any(h["code"] == r["code"] for h in held):
                    continue
                alloc = min(value / slots, cash)
                if alloc <= 0:
                    break
                sh = alloc / (r["entry"] * (1 + FEE))
                cash -= alloc
                held.append({"code": r["code"], "k": colpos[r["code"]], "sh": sh,
                             "x": pos[r["exit_date"]], "px": r["exit"], "last": r["entry"]})
        for h in held:
            v = Cn[t, h["k"]]
            if not np.isnan(v):
                h["last"] = v
        eq.append(cash + sum(h["sh"] * h["last"] for h in held))
    return pd.Series(eq, index=dates[start:end], name="淨值")


def equity_stats(eq: pd.Series) -> dict:
    if len(eq) < 2:
        return {}
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    return {"cagr": float(eq.iloc[-1] ** (1 / yrs) - 1),
            "mdd": float((eq / eq.cummax() - 1).min()),
            "total": float(eq.iloc[-1] - 1)}


# ── CLI ───────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="選股策略回測（目前預設規則）")
    ap.add_argument("--refresh", action="store_true", help="強制重新下載行情")
    a = ap.parse_args()
    ind = indicators(load_market(refresh=a.refresh))
    for name, sig, ex in [("動能突破（預設）", momentum_signal(ind), "ma20"),
                          ("趨勢轉強（樂透區）", lottery_signal(ind), "ma20")]:
        tr = simulate(sig, ind, ex)
        s = summarize(tr)
        print(f"\n【{name}】{fmt(s)}\n  判定：{verdict(s)}")


if __name__ == "__main__":
    main()
