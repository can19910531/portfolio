"""
big_holders.py — 400 張以上大戶持股比例（集保中心股權分散表）

為什麼要自己存檔：
    集保官網只保留**最近 51 週**，過了就永遠查不到；免費 API 也沒有長期歷史。
    要在一兩年後驗證「大戶在買」到底有沒有用，唯一的辦法是從現在開始每週存下來。
    存檔 `data/big_holders.csv` 是公開資料、但**無法重新取得**——請自行備份 data/ 資料夾。

用法：
    python big_holders.py                 # 抓最新一週（開放資料，全市場一次）並存檔
    python big_holders.py --backfill 2330 2317   # 從集保官網回補指定股票的 51 週歷史
    python big_holders.py --backfill-file list.txt  # 回補檔案內的代號（一行一個，可中斷續跑）

大戶定義：持股 400,001 股（400 張）以上的級距合計（第 12～15 級）。
"""
import argparse
import csv
import http.cookiejar
import io
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
STORE = HERE / "data" / "big_holders.csv"
COLUMNS = ["date", "code", "pct_400", "holders_400", "pct_1000"]

OPENDATA = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
QRY = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"
UA = "Mozilla/5.0"
BIG_LEVELS = {"12", "13", "14", "15"}          # 開放資料的級距編號：400,001 股以上
REQUEST_GAP = 1.2                              # 官網查詢間隔（秒），客氣一點避免被擋
# 只存股票（4 碼）與 ETF（00 開頭，如 00919、00940）；權證、特別股等不需要，免得存檔越長越大
KEEP_CODE = re.compile(r"^\d{4}$|^00\d{2,4}[A-Z]?$")


def _num(s: str) -> float:
    try:
        return float(str(s).replace(",", "").strip())
    except ValueError:
        return 0.0


def load_store() -> pd.DataFrame:
    if STORE.exists():
        return pd.read_csv(STORE, dtype={"date": str, "code": str})
    return pd.DataFrame(columns=COLUMNS)


def save_store(df: pd.DataFrame):
    STORE.parent.mkdir(exist_ok=True)
    df = df[df["code"].astype(str).str.match(KEEP_CODE)]
    df = df.drop_duplicates(["date", "code"], keep="last").sort_values(["code", "date"])
    df.to_csv(STORE, index=False, encoding="utf-8")


# ── 最新一週：開放資料（全市場一個檔案）────────────────────────────────
def fetch_latest() -> pd.DataFrame:
    req = urllib.request.Request(OPENDATA, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        text = r.read().decode("utf-8-sig", "ignore")
    agg = {}
    for row in list(csv.reader(io.StringIO(text)))[1:]:
        if len(row) < 6:
            continue
        date, code, lvl = row[0].strip(), row[1].strip(), row[2].strip()
        a = agg.setdefault((date, code), [0.0, 0.0, 0.0])
        if lvl in BIG_LEVELS:
            a[0] += _num(row[5])
            a[1] += _num(row[3])
        if lvl == "15":
            a[2] += _num(row[5])
    return pd.DataFrame([(d, c, round(p, 2), int(h), round(p1, 2))
                         for (d, c), (p, h, p1) in agg.items()], columns=COLUMNS)


# ── 歷史：集保官網查詢（一次一檔一週，最近 51 週）────────────────────────
class TdccSite:
    def __init__(self):
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.op.addheaders = [("User-Agent", UA)]
        html = self._open()
        self.token = self._token(html)
        self.weeks = re.findall(r'<option value="(\d{8})"', html)
        self.fir = self.weeks[0] if self.weeks else ""

    def _open(self, data: dict | None = None) -> str:
        body = urllib.parse.urlencode(data).encode() if data else None
        with self.op.open(QRY, body, timeout=60) as r:
            return r.read().decode("utf-8", "ignore")

    @staticmethod
    def _token(html: str) -> str:
        m = re.search(r'name="SYNCHRONIZER_TOKEN" value="([^"]+)"', html)
        if not m:
            raise RuntimeError("[錯誤] 集保官網頁面格式變了（找不到查詢憑證），需要更新程式")
        return m.group(1)

    def query(self, code: str, week: str) -> tuple | None:
        html = self._open({"SYNCHRONIZER_TOKEN": self.token,
                           "SYNCHRONIZER_URI": "/portal/zh/smWeb/qryStock",
                           "method": "submit", "firDate": self.fir, "sqlMethod": "StockNo",
                           "stockNo": code, "stockName": "", "scaDate": week})
        self.token = self._token(html)
        pct = hold = pct1000 = 0.0
        found = False
        for tr in re.findall(r"<tr>(.*?)</tr>", html, re.S):
            c = [re.sub(r"<[^>]+>|\s+", "", x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if len(c) != 5 or not c[0].isdigit():
                continue
            found = True
            low = re.match(r"([\d,]+)", c[1])          # 級距文字，如 400,001-600,000、1,000,001以上
            if low and _num(low.group(1)) >= 400001:
                pct += _num(c[4])
                hold += _num(c[2])
                if _num(low.group(1)) >= 1000001:
                    pct1000 += _num(c[4])
        return (week, code, round(pct, 2), int(hold), round(pct1000, 2)) if found else None


def backfill(codes: list[str]):
    """回補 51 週歷史；已存在的 (週, 代號) 會跳過，可中斷後續跑"""
    site = TdccSite()
    have = load_store()
    done = set(zip(have["date"], have["code"]))
    todo = [(c, w) for c in codes for w in site.weeks if (w, c) not in done]
    print(f"官網可查 {len(site.weeks)} 週（{site.weeks[-1]}～{site.weeks[0]}）；"
          f"{len(codes)} 檔共需查 {len(todo)} 次，預估 {len(todo) * (REQUEST_GAP + 0.4) / 3600:.1f} 小時",
          flush=True)
    buf, fails = [], 0
    for n, (code, week) in enumerate(todo, 1):
        for attempt in range(4):
            try:
                r = site.query(code, week)
                if r:
                    buf.append(r)
                break
            except Exception as e:
                fails += 1
                time.sleep(30 * (attempt + 1))
                try:
                    site = TdccSite()                    # 重新取得連線與憑證
                except Exception:
                    pass
                if attempt == 3:
                    print(f"  ! {code}@{week} 放棄：{type(e).__name__}", flush=True)
        if len(buf) >= 200 or n == len(todo):
            save_store(pd.concat([load_store(), pd.DataFrame(buf, columns=COLUMNS)]))
            buf = []
            print(f"  進度 {n}/{len(todo)}（{n / len(todo):.0%}），重試 {fails} 次", flush=True)
        time.sleep(REQUEST_GAP)


def ensure_recent(codes: list[str], n_weeks: int = 2) -> list[str]:
    """確保這些股票有集保官網「最新 n 週」的資料（缺的就上官網查）。回傳那 n 週（新→舊）。

    為什麼不靠開放資料：2026-10-22 實測，開放資料的「最新一週」會倒退——中午給 10/16、
    下午變回 10/08。官網的週次清單才是權威的，選股前一定要用它對齊。"""
    site = TdccSite()
    weeks = site.weeks[:n_weeks]
    have = load_store()
    done = set(zip(have["date"], have["code"]))
    todo = [(c, w) for c in codes for w in weeks if (w, c) not in done]
    rows = []
    for c, w in todo:
        try:
            r = site.query(c, w)
            if r:
                rows.append(r)
        except Exception as e:
            print(f"  ! 官網查 {c}@{w} 失敗：{type(e).__name__}", flush=True)
        time.sleep(REQUEST_GAP)
    if rows:
        save_store(pd.concat([load_store(), pd.DataFrame(rows, columns=COLUMNS)]))
    print(f"OK 大戶資料對齊官網最新 {n_weeks} 週 {weeks}：補查 {len(todo)} 筆", flush=True)
    return weeks


def big_holder_change(store: pd.DataFrame | None = None) -> pd.DataFrame:
    """每檔每週的 400 張以上持股比例，加上「比上週增減」(百分點)"""
    df = load_store() if store is None else store.copy()
    df = df.sort_values(["code", "date"])
    df["chg_400"] = df.groupby("code")["pct_400"].diff()
    return df


def main():
    ap = argparse.ArgumentParser(description="400 張以上大戶持股（集保股權分散表）")
    ap.add_argument("--backfill", nargs="*", help="回補指定代號的 51 週歷史")
    ap.add_argument("--backfill-file", help="回補檔案中的代號（一行一個）")
    a = ap.parse_args()

    if a.backfill or a.backfill_file:
        codes = list(a.backfill or [])
        if a.backfill_file:
            codes += [ln.strip() for ln in Path(a.backfill_file).read_text(encoding="utf-8").splitlines()
                      if ln.strip()]
        backfill(sorted(set(codes)))
        return

    latest = fetch_latest()
    save_store(pd.concat([load_store(), latest]))
    print(f"OK 存檔 {latest['date'].iloc[0]} 當週 {len(latest)} 檔 → {STORE}")


if __name__ == "__main__":
    main()
