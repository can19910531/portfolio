"""
quotes.py — 抓指數與自選股行情，當作 AI 撰寫市場重點時的「事實基準」

用法：
    python quotes.py                 # 依 config.yaml 的 market / watchlist
    python quotes.py ^TWII NVDA      # 指定代號

為什麼需要這支：
    LLM 憑印象寫數字是幻覺重災區（指數點數、漲跌幅、日期都可能編出來）。
    brief.py 會先跑這支把真實數字抓好，塞進 prompt 並明令「照抄不准自填」，
    等於把「能用程式算的」從 AI 手上拿回來。這是三層防護的第 1 層。

輸出：最近收盤價、單日漲跌幅、本週漲跌幅（vs 上週最後一個交易日）。
資料來源：Yahoo Finance（yfinance）。
"""
import sys
from pathlib import Path

import pandas as pd
import yaml
import yfinance as yf

HERE = Path(__file__).parent

TW_INDEX = [("^TWII", "台股加權")]
US_INDEX = [("^DJI", "道瓊"), ("^GSPC", "標普500"),
            ("^IXIC", "那斯達克"), ("^SOX", "費城半導體")]


def load_config() -> dict:
    f = HERE / "config.yaml"
    if not f.exists():
        return {}
    return yaml.safe_load(f.read_text(encoding="utf-8")) or {}


def default_targets(cfg: dict) -> list[tuple[str, str]]:
    """依 config 決定要抓哪些標的：大盤指數 + 自選股"""
    market = str(cfg.get("market", "both")).lower()
    out: list[tuple[str, str]] = []
    if market in ("tw", "both"):
        out += TW_INDEX
    if market in ("us", "both"):
        out += US_INDEX
    for w in cfg.get("watchlist", []) or []:
        code = str(w.get("symbol", "")).strip()
        if not code:
            continue
        mkt = str(w.get("market", "tw")).lower()
        ticker = f"{code}.TW" if mkt == "tw" else code
        out.append((ticker, str(w.get("name", "") or code).strip()))
    return out


def week_change(close: pd.Series):
    """本週漲跌幅：最新收盤 vs 上一個週一之前的最後一筆收盤"""
    last_date = close.index[-1]
    monday = last_date - pd.Timedelta(days=last_date.weekday())
    prior = close[close.index < monday]
    if prior.empty:
        return None
    return (close.iloc[-1] / prior.iloc[-1] - 1) * 100


def quote_lines(targets: list[tuple[str, str]]) -> list[str]:
    lines = []
    for ticker, name in targets:
        try:
            close = yf.Ticker(ticker).history(period="2mo")["Close"].dropna()
        except Exception as e:  # 網路或代號問題
            print(f"WARN {ticker}: {e}")
            continue
        if len(close) < 2:
            print(f"WARN {ticker}: 資料不足")
            continue
        day = (close.iloc[-1] / close.iloc[-2] - 1) * 100
        wk = week_change(close)
        wk_txt = f"{wk:+.2f}%" if wk is not None else "n/a"
        lines.append(
            f"{ticker:10s} {name:10s} {close.index[-1].strftime('%Y-%m-%d')} "
            f"收 {close.iloc[-1]:,.2f}  日 {day:+.2f}%  週 {wk_txt}")
    return lines


def main():
    cfg = load_config()
    if len(sys.argv) > 1:
        targets = [(t, t) for t in sys.argv[1:]]
    else:
        targets = default_targets(cfg)
        if not targets:
            print("X config.yaml 沒有 market/watchlist，也沒指定代號")
            sys.exit(2)
    for line in quote_lines(targets):
        print(line)


if __name__ == "__main__":
    main()
