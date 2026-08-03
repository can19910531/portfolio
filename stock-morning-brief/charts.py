"""
charts.py — 產生晨報配圖（K線＋技術指標）＋自選股健檢表

用法：
    python charts.py                  # 產今天的圖與健檢表
    python charts.py --date 2026-08-02

設定：同資料夾 config.yaml（沒有的話先複製 config.example.yaml，
      或讓 AI 讀 docs/初始設定任務書.md 訪談後幫你產生）。

資料來源：Yahoo Finance（yfinance，免費、免金鑰）。
產出（<archive_dir>/YYYY-MM-DD_圖/）：
    01_台股加權.png       加權指數 K線＋指標（market 含 tw 時）
    02_美股指數.png       道瓊/標普/那指/費半 最近交易日漲跌幅（market 含 us 時）
    03_自選_{代號}.png    每檔自選股 K線＋指標
另產出：<archive_dir>/YYYY-MM-DD_健檢.md（短/中/長期趨勢箭頭，程式計算）
"""
import sys
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
import yaml
import yfinance as yf

HERE = Path(__file__).parent


def load_config():
    f = HERE / "config.yaml"
    if not f.exists():
        print("X 找不到 config.yaml。請複製 config.example.yaml 改名為 config.yaml，"
              "或讓你的 AI 讀 docs/初始設定任務書.md 幫你完成設定。")
        sys.exit(2)
    cfg = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    cfg.setdefault("market", "both")
    cfg.setdefault("watchlist", [])
    cfg.setdefault("indicators", ["kline", "macd"])
    cfg.setdefault("storage", "local")
    cfg.setdefault("archive_dir", "./archive")
    cfg.setdefault("show_bars", 60)
    cfg.setdefault("color_convention", "tw")
    return cfg


CFG = load_config()

plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "PingFang TC", "Noto Sans CJK TC", "Arial"]
plt.rcParams["axes.unicode_minus"] = False

# 漲跌顏色：台股習慣紅漲綠跌；美股習慣綠漲紅跌
if CFG["color_convention"] == "us":
    UP, DOWN = "#2f9e44", "#d64545"
else:
    UP, DOWN = "#d64545", "#2f9e44"

_MC = mpf.make_marketcolors(up=UP, down=DOWN, edge="inherit", wick="inherit")
_STYLE = mpf.make_mpf_style(
    marketcolors=_MC, gridstyle=":", gridcolor="#cccccc",
    rc={"font.sans-serif": plt.rcParams["font.sans-serif"], "axes.unicode_minus": False})

SHOW_BARS = int(CFG["show_bars"])


def watchlist():
    """config.yaml 的 watchlist → [{ticker,label,code}, ...]"""
    out = []
    for w in CFG["watchlist"]:
        code = str(w.get("symbol", "")).strip()
        if not code:
            continue
        mkt = str(w.get("market", "tw")).lower()
        name = str(w.get("name", "") or code).strip()
        out.append({
            "code": code,
            "ticker": f"{code}.TW" if mkt == "tw" else code,
            "label": f"{code} {name}" if name != code else code,
            "name": name,
        })
    return out


def macd(close: pd.Series):
    """回傳 (DIF 快線, DEA 慢線, 柱狀)"""
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    return dif, dea, dif - dea


def fetch(ticker):
    """抓 2 年日線（健檢長期訊號要用）；失敗回 None"""
    try:
        df = yf.Ticker(ticker).history(period="2y")
    except Exception as e:
        print(f"[warn] {ticker} 抓價失敗：{e}")
        return None
    if df is None or df.empty or len(df) < 30:
        print(f"[warn] {ticker} 資料不足")
        return None
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna()


def trend_arrows(df):
    """算 短/中/長期 趨勢箭頭（各由 5 個技術訊號投票，映射到 ▲/▼ 1~5 個）
    短期：價 vs MA5/MA10、MA5 斜率、DIF vs DEA、DIF 斜率
    中期：價 vs MA20/MA60、MA20 斜率、MA20 vs MA60、DIF 正負
    長期：價 vs MA120、MA120 斜率、價在52週區間上/下半、120日報酬、價 vs MA200
    回傳 (短, 中, 長) 字串"""
    c = df["Close"]
    last = c.iloc[-1]
    ma = {n: c.rolling(n).mean() for n in (5, 10, 20, 60, 120, 200)}
    dif, dea, _ = macd(c)

    def vote(signals):
        sigs = [s for s in signals if s is not None]
        if not sigs:
            return "—"
        score = round(5 * sum(1 if s else -1 for s in sigs) / len(sigs))
        if score > 0:
            return "▲" * min(score, 5)
        if score < 0:
            return "▼" * min(-score, 5)
        return "→"

    def ok(series, idx=-1):
        try:
            v = series.iloc[idx]
            return None if pd.isna(v) else v
        except (IndexError, KeyError):
            return None

    def cmp(a, b):
        return None if a is None or b is None else bool(a > b)

    short = vote([
        cmp(last, ok(ma[5])), cmp(last, ok(ma[10])),
        cmp(ok(ma[5]), ok(ma[5], -4)),
        cmp(ok(dif), ok(dea)), cmp(ok(dif), ok(dif, -4)),
    ])
    mid = vote([
        cmp(last, ok(ma[20])), cmp(last, ok(ma[60])),
        cmp(ok(ma[20]), ok(ma[20], -11)),
        cmp(ok(ma[20]), ok(ma[60])),
        None if ok(dif) is None else bool(ok(dif) > 0),
    ])
    year = c.iloc[-252:] if len(c) >= 252 else c
    mid52 = (year.max() + year.min()) / 2
    long_ = vote([
        cmp(last, ok(ma[120])),
        cmp(ok(ma[120]), ok(ma[120], -21)),
        bool(last > mid52),
        cmp(last, ok(c, -121)) if len(c) > 121 else None,
        cmp(last, ok(ma[200])),
    ])
    return short, mid, long_


def kline_chart(df, title, outfile):
    """K線（近 SHOW_BARS 根）＋config 指定的指標。回傳最新收盤價或 None"""
    if df is None:
        return None
    inds = CFG["indicators"]
    view = df.iloc[-SHOW_BARS:]

    last = float(df["Close"].iloc[-1])
    prev = float(df["Close"].iloc[-2])
    day_chg = (last / prev - 1) * 100

    aps = []
    panels = 1
    if "ma" in inds:  # 均線疊在主圖
        for n, color in [(5, "#f59f00"), (20, "#7048e8"), (60, "#1098ad")]:
            m = df["Close"].rolling(n).mean().iloc[-SHOW_BARS:]
            if not m.dropna().empty:
                aps.append(mpf.make_addplot(m, panel=0, color=color, width=1.0))
    if "macd" in inds:  # MACD 副圖（用完整資料算，避免起頭失真）
        dif, dea, hist = macd(df["Close"])
        dif_v, dea_v, hist_v = dif.iloc[-SHOW_BARS:], dea.iloc[-SHOW_BARS:], hist.iloc[-SHOW_BARS:]
        hist_colors = [UP if v >= 0 else DOWN for v in hist_v]
        aps += [
            mpf.make_addplot(dif_v, panel=1, color="#e8590c", width=1.6, ylabel="MACD"),
            mpf.make_addplot(dea_v, panel=1, color="#4263eb", width=0.9, alpha=0.7),
            mpf.make_addplot(hist_v, panel=1, type="bar", color=hist_colors, alpha=0.35),
        ]
        panels = 2

    kw = dict(type="candle", style=_STYLE, figsize=(9, 6.5), returnfig=True,
              datetime_format="%m/%d", tight_layout=True)
    if aps:
        kw["addplot"] = aps
    if panels == 2:
        kw["panel_ratios"] = (3, 1)
    fig, axes = mpf.plot(view, **kw)
    axes[0].set_title(f"{title}　收 {last:,.2f}（{day_chg:+.2f}%）　近{SHOW_BARS}日K", fontsize=13)
    if panels == 2:
        axes[2].legend(["DIF 快線", "DEA 慢線"], loc="upper left", fontsize=8, frameon=False)
    fig.savefig(outfile, dpi=120)
    plt.close(fig)
    print(f"OK {outfile.name}")
    return last


def us_index_bar(outfile):
    """美股四大指數：最近一個交易日漲跌幅長條圖"""
    indexes = [("^DJI", "道瓊"), ("^GSPC", "標普500"), ("^IXIC", "那斯達克"), ("^SOX", "費半")]
    names, chgs = [], []
    for tk, name in indexes:
        try:
            close = yf.Ticker(tk).history(period="5d")["Close"].dropna()
            if len(close) >= 2:
                names.append(name)
                chgs.append((close.iloc[-1] / close.iloc[-2] - 1) * 100)
        except Exception as e:
            print(f"[warn] {tk} 失敗：{e}")
    if not names:
        return False
    colors = [UP if c >= 0 else DOWN for c in chgs]
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=120)
    bars = ax.bar(names, chgs, color=colors, width=0.55)
    for b, c in zip(bars, chgs):
        ax.text(b.get_x() + b.get_width() / 2,
                b.get_height() + (0.02 if c >= 0 else -0.08),
                f"{c:+.2f}%", ha="center",
                va="bottom" if c >= 0 else "top", fontsize=11)
    ax.set_title("美股四大指數　最近交易日漲跌幅", fontsize=13)
    ax.axhline(0, color="#888", linewidth=0.8)
    ax.grid(axis="y", alpha=0.25)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(outfile)
    plt.close(fig)
    print(f"OK {outfile.name}")
    return True


def write_health(rows, outfile):
    """產自選股健檢表。rows: [(代號, 名稱, 現價, 日漲跌%, 短, 中, 長)]"""
    lines = [
        "🩺 自選股健檢",
        "▲越多越偏多、▼越多越偏空（最多5個；技術面訊號統計，非投資建議）",
        "",
    ]
    for code, name, px, chg, s, m, l in rows:
        lines.append(f"{code} {name}｜收 {px:,.2f}（{chg:+.2f}%）")
        lines.append(f"　短期 {s}｜中期 {m}｜長期 {l}")
    lines.append("")
    outfile.write_text("\n".join(lines), encoding="utf-8")
    print(f"OK {outfile.name}")


def main():
    day = date.today().isoformat()
    if "--date" in sys.argv:
        day = sys.argv[sys.argv.index("--date") + 1]
    archive = (HERE / CFG["archive_dir"]).resolve()
    outdir = archive / f"{day}_圖"
    outdir.mkdir(parents=True, exist_ok=True)

    market = str(CFG["market"]).lower()
    made = []

    if market in ("tw", "both"):
        twii = fetch("^TWII")
        if kline_chart(twii, "台股加權指數", outdir / "01_台股加權.png") is not None:
            made.append(outdir / "01_台股加權.png")
    if market in ("us", "both"):
        if us_index_bar(outdir / "02_美股指數.png"):
            made.append(outdir / "02_美股指數.png")

    health = []
    for h in watchlist():
        safe = h["ticker"].replace(".", "_").replace("^", "")
        f = outdir / f"03_自選_{safe}.png"
        df = fetch(h["ticker"])
        px = kline_chart(df, h["label"], f)
        if px is not None:
            made.append(f)
            chg = (df["Close"].iloc[-1] / df["Close"].iloc[-2] - 1) * 100
            s, m, l = trend_arrows(df)
            health.append((h["code"], h["name"], px, chg, s, m, l))

    write_health(health, archive / f"{day}_健檢.md")

    print(f"完成：共 {len(made)} 張圖 → {outdir}")
    for f in sorted(made):
        print("IMG:", f)


if __name__ == "__main__":
    main()
