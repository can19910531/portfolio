# -*- coding: utf-8 -*-
"""
斷貨查詢小幫手.py — 團購斷貨退款查詢網站的一鍵操作視窗（CustomTkinter）

不用打指令：雙擊開啟 → 自動掃描有哪些月團 → 選月團 → 按「更新並部署」，
網站資料就換成該月團；要收款前按「匯出批次退款表」。

底層直接呼叫同資料夾的 更新斷貨查詢網站.py／匯出批次退款表.py，邏輯零重複。

啟動：  python 斷貨查詢小幫手.py
截圖：  python 斷貨查詢小幫手.py --screenshot 輸出.png   （作品集展示用）
"""
import calendar
import datetime
import glob
import json
import os
import queue
import re
import subprocess
import sys
import threading
import webbrowser

import customtkinter as ctk

# 打包成 exe 時（PyInstaller），程式位置要以 exe 所在資料夾為準，而不是解壓的暫存夾
if getattr(sys, "frozen", False):
    BASE = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)


def _python_exe():
    """跑底層腳本用的 Python：exe 模式下 sys.executable 是 exe 自己，要另找系統的 python。"""
    if not getattr(sys, "frozen", False):
        return sys.executable
    import shutil
    p = shutil.which("python") or shutil.which("py")
    if not p:
        return None
    return p

# ── 品牌色（跟查詢網站同一套奶油×珊瑚橘）──
CREAM = "#FFF6EA"
PAPER = "#FFFDF8"
INK = "#4A382B"
INK_SOFT = "#8A7261"
CORAL = "#F2704F"
CORAL_DEEP = "#D9532F"
MINT = "#2FA383"
LINE = "#EAD9C4"
RED = "#D93025"

ctk.set_appearance_mode("light")


def load_cfg():
    try:
        with open(os.path.join(BASE, "設定.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_upd():
    """載入 更新斷貨查詢網站.py 模組，共用它的 run_gws（試算表讀寫）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_upd", os.path.join(BASE, "更新斷貨查詢網站.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def parse_date(text):
    """接受 2026-09-10 / 2026/9/10 / 9/10（補今年）等寫法，回傳 date 或 None。"""
    t = str(text or "").strip().replace("－", "-").replace("．", ".")
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})$", t)
    if m:
        today = datetime.date.today()
        try:
            d = datetime.date(today.year, int(m.group(1)), int(m.group(2)))
            return d if d >= today else datetime.date(today.year + 1, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


class CalendarPopup(ctk.CTkToplevel):
    """點選式月曆（大按鈕，好按）。選好日期回呼 on_pick(date)。"""

    def __init__(self, master, init_date=None, on_pick=None):
        super().__init__(master)
        self.title("選退款期限")
        self.configure(fg_color=PAPER)
        self.resizable(False, False)
        self.on_pick = on_pick
        d = init_date or datetime.date.today()
        self.year, self.month = d.year, d.month
        self.transient(master)
        self.grab_set()
        # 開在主視窗中間
        self.update_idletasks()
        x = master.winfo_rootx() + master.winfo_width() // 2 - 190
        y = master.winfo_rooty() + 160
        self.geometry(f"380x420+{max(x, 0)}+{max(y, 0)}")
        self._draw()

    def _draw(self):
        for w in self.winfo_children():
            w.destroy()
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(14, 6))
        ctk.CTkButton(head, text="◀", width=44, height=40, command=lambda: self._move(-1),
                      fg_color=LINE, hover_color="#DCC5A8", text_color=INK,
                      font=ctk.CTkFont("Microsoft JhengHei UI", 16, "bold")).pack(side="left")
        ctk.CTkLabel(head, text=f"{self.year} 年 {self.month} 月",
                     font=ctk.CTkFont("Microsoft JhengHei UI", 18, "bold"),
                     text_color=INK).pack(side="left", expand=True)
        ctk.CTkButton(head, text="▶", width=44, height=40, command=lambda: self._move(1),
                      fg_color=LINE, hover_color="#DCC5A8", text_color=INK,
                      font=ctk.CTkFont("Microsoft JhengHei UI", 16, "bold")).pack(side="right")

        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.pack(padx=14, pady=(0, 14))
        for i, wd in enumerate(["一", "二", "三", "四", "五", "六", "日"]):
            ctk.CTkLabel(grid, text=wd, width=46,
                         font=ctk.CTkFont("Microsoft JhengHei UI", 12, "bold"),
                         text_color=(RED if i >= 5 else INK_SOFT)).grid(row=0, column=i, pady=(0, 4))
        today = datetime.date.today()
        for r, week in enumerate(calendar.Calendar().monthdayscalendar(self.year, self.month), start=1):
            for c, day in enumerate(week):
                if day == 0:
                    continue
                d = datetime.date(self.year, self.month, day)
                is_today = d == today
                btn = ctk.CTkButton(
                    grid, text=str(day), width=46, height=44,
                    command=lambda dd=d: self._pick(dd),
                    fg_color=(CORAL if is_today else CREAM),
                    text_color=("#FFFFFF" if is_today else INK),
                    hover_color=CORAL_DEEP,
                    border_width=0 if is_today else 1, border_color=LINE,
                    font=ctk.CTkFont("Microsoft JhengHei UI", 14, "bold" if is_today else "normal"))
                btn.grid(row=r, column=c, padx=2, pady=2)

    def _move(self, step):
        m = self.month + step
        if m < 1:
            self.year, self.month = self.year - 1, 12
        elif m > 12:
            self.year, self.month = self.year + 1, 1
        else:
            self.month = m
        self._draw()

    def _pick(self, d):
        if self.on_pick:
            self.on_pick(d)
        self.destroy()


def scan_months(monthly_dir):
    """掃描月團資料夾，回傳 [(名稱, 有出貨明細, 有斷貨單)]，新的在前。"""
    out = []
    if not os.path.isdir(monthly_dir):
        return out
    for d in sorted(glob.glob(os.path.join(monthly_dir, "????-??_*月團")), reverse=True):
        detail = [f for f in glob.glob(os.path.join(d, "出貨單", "*出貨明細*.xlsx"))
                  if "核對" not in os.path.basename(f) and not os.path.basename(f).startswith("~$")]
        shortage = [f for f in glob.glob(os.path.join(d, "斷貨單", "*斷貨單*.xlsx"))
                    if not os.path.basename(f).startswith("~$")]
        out.append((os.path.basename(d), bool(detail), bool(shortage)))
    return out


class App(ctk.CTk):
    def __init__(self, demo=False):
        super().__init__()
        self.title("團購斷貨查詢網站小幫手")
        self.geometry("720x700")
        self.minsize(640, 620)
        self.configure(fg_color=CREAM)
        self.cfg = load_cfg()
        self.months = []
        self.running = False
        self.log_q = queue.Queue()
        self._upd = None
        self._build()
        self.rescan()
        self.after(100, self._drain_log)
        if not demo:
            threading.Thread(target=self._load_deadline, daemon=True).start()

    # ── 版面 ──
    def _build(self):
        pad = {"padx": 24, "pady": (0, 0)}

        head = ctk.CTkLabel(self, text="🧡 團購斷貨查詢網站小幫手",
                            font=ctk.CTkFont("Microsoft JhengHei UI", 22, "bold"),
                            text_color=INK)
        head.pack(anchor="w", padx=24, pady=(20, 2))
        sub = ctk.CTkLabel(self, text="選好月團按下部署，查詢網站的內容就會換成那個月的資料（網址永遠不變）",
                           font=ctk.CTkFont("Microsoft JhengHei UI", 12),
                           text_color=INK_SOFT)
        sub.pack(anchor="w", padx=24, pady=(0, 14))

        card = ctk.CTkFrame(self, fg_color=PAPER, corner_radius=16,
                            border_width=1, border_color=LINE)
        card.pack(fill="x", **pad)

        row1 = ctk.CTkFrame(card, fg_color="transparent")
        row1.pack(fill="x", padx=16, pady=(16, 8))
        ctk.CTkLabel(row1, text="每月資料位置", width=96, anchor="w",
                     font=ctk.CTkFont("Microsoft JhengHei UI", 13, "bold"),
                     text_color=INK).pack(side="left")
        self.path_var = ctk.StringVar(value=os.path.join(ROOT, "每月資料"))
        self.path_entry = ctk.CTkEntry(row1, textvariable=self.path_var,
                                       font=ctk.CTkFont("Microsoft JhengHei UI", 12),
                                       fg_color=CREAM, border_color=LINE, text_color=INK)
        self.path_entry.pack(side="left", fill="x", expand=True, padx=(8, 8))
        ctk.CTkButton(row1, text="瀏覽…", width=72, command=self.browse,
                      fg_color=LINE, hover_color="#DCC5A8", text_color=INK,
                      font=ctk.CTkFont("Microsoft JhengHei UI", 12)).pack(side="left")

        row2 = ctk.CTkFrame(card, fg_color="transparent")
        row2.pack(fill="x", padx=16, pady=(8, 6))
        ctk.CTkLabel(row2, text="月團", width=96, anchor="w",
                     font=ctk.CTkFont("Microsoft JhengHei UI", 13, "bold"),
                     text_color=INK).pack(side="left")
        self.month_var = ctk.StringVar(value="（掃描中…）")
        self.month_menu = ctk.CTkOptionMenu(row2, variable=self.month_var, values=["—"],
                                            command=lambda _v: self.update_status(),
                                            fg_color=CORAL, button_color=CORAL_DEEP,
                                            button_hover_color="#B4401F",
                                            font=ctk.CTkFont("Microsoft JhengHei UI", 13, "bold"),
                                            dropdown_font=ctk.CTkFont("Microsoft JhengHei UI", 12))
        self.month_menu.pack(side="left", padx=(8, 8))
        ctk.CTkButton(row2, text="↻ 重新掃描", width=96, command=self.rescan,
                      fg_color=LINE, hover_color="#DCC5A8", text_color=INK,
                      font=ctk.CTkFont("Microsoft JhengHei UI", 12)).pack(side="left")
        self.status_lbl = ctk.CTkLabel(row2, text="", font=ctk.CTkFont("Microsoft JhengHei UI", 12),
                                       text_color=INK_SOFT)
        self.status_lbl.pack(side="left", padx=(12, 0))

        row3 = ctk.CTkFrame(card, fg_color="transparent")
        row3.pack(fill="x", padx=16, pady=(8, 6))
        ctk.CTkLabel(row3, text="退款期限", width=96, anchor="w",
                     font=ctk.CTkFont("Microsoft JhengHei UI", 13, "bold"),
                     text_color=INK).pack(side="left")
        self.deadline_var = ctk.StringVar(value="")
        self.deadline_entry = ctk.CTkEntry(row3, textvariable=self.deadline_var, width=130,
                                           placeholder_text="讀取中…",
                                           font=ctk.CTkFont("Microsoft JhengHei UI", 13),
                                           fg_color=CREAM, border_color=LINE, text_color=INK)
        self.deadline_entry.pack(side="left", padx=(8, 8))
        ctk.CTkButton(row3, text="📅 選日期", width=92, command=self.pick_deadline,
                      fg_color=CORAL, hover_color=CORAL_DEEP,
                      font=ctk.CTkFont("Microsoft JhengHei UI", 12, "bold")).pack(side="left", padx=(0, 8))
        self.btn_save_dl = ctk.CTkButton(row3, text="💾 儲存期限", width=96, command=self.save_deadline,
                                         fg_color=MINT, hover_color="#22715B",
                                         font=ctk.CTkFont("Microsoft JhengHei UI", 12, "bold"))
        self.btn_save_dl.pack(side="left")
        ctk.CTkLabel(row3, text="存檔後手機網站立即生效", font=ctk.CTkFont("Microsoft JhengHei UI", 11),
                     text_color=INK_SOFT).pack(side="left", padx=(10, 0))

        rowb = ctk.CTkFrame(card, fg_color="transparent")
        rowb.pack(fill="x", padx=16, pady=(10, 16))
        self.btn_deploy = ctk.CTkButton(rowb, text="🚀 更新並部署查詢網站", height=44,
                                        command=lambda: self.run_job("deploy"),
                                        fg_color=CORAL, hover_color=CORAL_DEEP,
                                        font=ctk.CTkFont("Microsoft JhengHei UI", 15, "bold"))
        self.btn_deploy.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.btn_export = ctk.CTkButton(rowb, text="📋 匯出批次退款表", height=44,
                                        command=lambda: self.run_job("export"),
                                        fg_color=MINT, hover_color="#22715B",
                                        font=ctk.CTkFont("Microsoft JhengHei UI", 15, "bold"))
        self.btn_export.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.btn_open = ctk.CTkButton(rowb, text="🌐 打開網站", height=44, width=110,
                                      command=self.open_site,
                                      fg_color=LINE, hover_color="#DCC5A8", text_color=INK,
                                      font=ctk.CTkFont("Microsoft JhengHei UI", 13, "bold"))
        self.btn_open.pack(side="left")

        ctk.CTkLabel(self, text="執行紀錄", font=ctk.CTkFont("Microsoft JhengHei UI", 13, "bold"),
                     text_color=INK).pack(anchor="w", padx=26, pady=(14, 4))
        self.log = ctk.CTkTextbox(self, fg_color="#2B241E", text_color="#F5E8D5",
                                  font=ctk.CTkFont("Consolas", 12), corner_radius=12)
        self.log.pack(fill="both", expand=True, padx=24, pady=(0, 20))
        self.log.configure(state="disabled")
        self._log("準備就緒。選好月團後按「🚀 更新並部署查詢網站」。")

    # ── 動作 ──
    def browse(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(initialdir=self.path_var.get() or ROOT, title="選擇 每月資料 資料夾")
        if d:
            self.path_var.set(os.path.normpath(d))
            self.rescan()

    def rescan(self):
        self.months = scan_months(self.path_var.get())
        if self.months:
            names = [m[0] for m in self.months]
            self.month_menu.configure(values=names)
            self.month_var.set(names[0])
        else:
            self.month_menu.configure(values=["—"])
            self.month_var.set("—")
        self.update_status()
        self._log(f"掃描 {self.path_var.get()} → 找到 {len(self.months)} 個月團資料夾")

    def update_status(self):
        cur = next((m for m in self.months if m[0] == self.month_var.get()), None)
        if not cur:
            self.status_lbl.configure(text="找不到月團資料夾", text_color=RED)
            return
        parts = [("出貨明細 ✓" if cur[1] else "出貨明細 ✗"), ("斷貨單 ✓" if cur[2] else "斷貨單 ✗")]
        ok = cur[1]
        self.status_lbl.configure(text="　".join(parts), text_color=(MINT if ok else RED))

    # ── 退款期限 ──
    def _get_upd(self):
        if self._upd is None:
            self._upd = load_upd()
        return self._upd

    def _load_deadline(self):
        """啟動時從試算表「設定」B3 讀目前的退款期限，填進欄位。"""
        try:
            upd = self._get_upd()
            cfg = load_cfg()
            out = upd.run_gws(["sheets", "spreadsheets", "values", "get"],
                              params={"spreadsheetId": cfg["spreadsheetId"],
                                      "range": f"{cfg.get('設定分頁', '設定')}!B3"},
                              allow_fail=True)
            if out:
                data = json.loads(out[out.index("{"):])
                val = (data.get("values") or [[""]])[0][0]
                self.deadline_var.set(str(val))
                self.log_q.put(f"目前設定的退款期限：{val or '（未設定）'}")
        except Exception as e:
            self.log_q.put(f"[提醒] 讀不到目前的退款期限（{e}），可直接填新的再按儲存。")

    def pick_deadline(self):
        CalendarPopup(self, init_date=parse_date(self.deadline_var.get()),
                      on_pick=lambda d: self.deadline_var.set(d.strftime("%Y-%m-%d")))

    def save_deadline(self, silent=False):
        d = parse_date(self.deadline_var.get())
        if not d:
            self._log("[錯誤] 退款期限看不懂，請用「📅 選日期」點選，或填 2026-09-10 這種格式。")
            return
        if d < datetime.date.today():
            self._log(f"[錯誤] 退款期限 {d} 已經是過去的日期了，請再確認。")
            return
        self.btn_save_dl.configure(state="disabled", text="儲存中…")
        threading.Thread(target=self._save_deadline_worker, args=(d,), daemon=True).start()

    def _save_deadline_worker(self, d):
        try:
            upd = self._get_upd()
            cfg = load_cfg()
            # 用 USER_ENTERED 寫 2026/09/10 → 試算表存成真正的日期，網站會顯示「9月10日」
            upd.run_gws(["sheets", "spreadsheets", "values", "update"],
                        params={"spreadsheetId": cfg["spreadsheetId"],
                                "range": f"{cfg.get('設定分頁', '設定')}!B3",
                                "valueInputOption": "USER_ENTERED"},
                        body={"values": [[d.strftime("%Y/%m/%d")]]})
            self.log_q.put(f"✅ 退款期限已存：{d.year}年{d.month}月{d.day}日（手機網站的紅字提醒立即生效）")
        except SystemExit:
            self.log_q.put("[錯誤] 退款期限存檔失敗，請確認網路後再按一次「💾 儲存期限」。")
        except Exception as e:
            self.log_q.put(f"[錯誤] 退款期限存檔失敗：{e}")
        self.log_q.put("__DL_DONE__")

    def open_site(self):
        url = load_cfg().get("pages_url") or ""
        if url:
            webbrowser.open(url)
            self._log(f"已在瀏覽器打開 {url}")
        else:
            self._log("[提醒] 設定.json 還沒有 pages_url，先部署一次網站。")

    def run_job(self, kind):
        if self.running:
            return
        month = self.month_var.get()
        cur = next((m for m in self.months if m[0] == month), None)
        if not cur:
            self._log("[錯誤] 請先選一個月團資料夾。")
            return
        if kind == "deploy" and not cur[1]:
            self._log(f"[錯誤] {month} 還沒有出貨明細（請先跑步驟5），無法部署。")
            return
        if kind == "deploy":
            d = parse_date(self.deadline_var.get())
            if not d or d < datetime.date.today():
                self._log("[提醒] 退款期限還沒設定（或已過期）——部署照常進行，記得用「📅 選日期」選好後按「💾 儲存期限」。")
        script = "更新斷貨查詢網站.py" if kind == "deploy" else "匯出批次退款表.py"
        month_path = os.path.join(self.path_var.get(), month)
        self.running = True
        self.btn_deploy.configure(state="disabled")
        self.btn_export.configure(state="disabled")
        self._log("")
        self._log(f"═══ 開始：{script} → {month} ═══")
        threading.Thread(target=self._worker, args=(script, month_path), daemon=True).start()

    def _worker(self, script, month_path):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        py = _python_exe()
        if not py:
            self.log_q.put("[錯誤] 找不到 Python，請確認電腦有安裝 Python（腳本執行需要它）。")
            self.log_q.put("__JOB_DONE__")
            return
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            p = subprocess.Popen([py, os.path.join(BASE, script), "--month", month_path],
                                 cwd=BASE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace", creationflags=flags)
            for line in p.stdout:
                self.log_q.put(line.rstrip())
            code = p.wait()
            self.log_q.put("═══ ✅ 完成 ═══" if code == 0 else f"═══ ⛔ 失敗（代碼 {code}），請看上方訊息 ═══")
        except Exception as e:
            self.log_q.put(f"[錯誤] 執行不了 {script}：{e}")
        self.log_q.put("__JOB_DONE__")

    def _drain_log(self):
        try:
            while True:
                line = self.log_q.get_nowait()
                if line == "__JOB_DONE__":
                    self.running = False
                    self.btn_deploy.configure(state="normal")
                    self.btn_export.configure(state="normal")
                elif line == "__DL_DONE__":
                    self.btn_save_dl.configure(state="normal", text="💾 儲存期限")
                else:
                    self._log(line)
        except queue.Empty:
            pass
        self.after(120, self._drain_log)

    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


def screenshot_mode(out_path):
    """作品集用：開視窗（填入示範資料）→ 存一張截圖 → 自動關閉。"""
    app = App(demo=True)
    app.path_var.set(r"D:\團購資料\每月資料")
    app.months = [("2026-07_7月團", True, True), ("2026-06_6月團", True, True)]
    app.month_menu.configure(values=[m[0] for m in app.months])
    app.month_var.set("2026-07_7月團")
    app.update_status()
    app.deadline_var.set("2026-08-15")
    app.log.configure(state="normal"); app.log.delete("1.0", "end"); app.log.configure(state="disabled")
    app._log("準備就緒。選好月團後按「🚀 更新並部署查詢網站」。")
    app._log("掃描 D:\團購資料\每月資料 → 找到 2 個月團資料夾")
    app._log("═══ 開始：更新斷貨查詢網站.py → 2026-07_7月團 ═══")
    app._log("[info] 月團：2026-07_7月團")
    app._log("[統計] 會員 140 位；有退款 33 位；退款總額 11304 元")
    app._log("[info] 清空 查詢資料 舊資料…")
    app._log("[info] 上傳 140 位會員資料…")
    app._log("  已上傳 140/140")
    app._log("[驗證] 試算表列數 140／本地 140；退款總額一致 ✅")
    app._log("[完成] 網站已部署：https://your-project.pages.dev")
    app._log("═══ ✅ 完成 ═══")

    def snap():
        from PIL import ImageGrab
        app.update_idletasks()
        x, y = app.winfo_rootx(), app.winfo_rooty()
        w, h = app.winfo_width(), app.winfo_height()
        ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(out_path)
        print(f"截圖已存 {out_path}")
        app.destroy()

    app.after(1200, snap)
    app.mainloop()


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--screenshot":
        screenshot_mode(sys.argv[2] if len(sys.argv) > 2 else "gui截圖.png")
    else:
        App().mainloop()
