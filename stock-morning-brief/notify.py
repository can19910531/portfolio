"""
notify.py — 把晨報推到 Telegram

用法：
    python notify.py "archive/2026-08-03_晨報.md"      # 推整份 markdown（自動分段）
    python notify.py --text "測試訊息"                  # 直接推一段文字（測試用）
    python notify.py --photos "archive/2026-08-03_圖"  # 推該資料夾所有 png

前提：同資料夾 .env 內有（照 .env.example 填）：
    TELEGRAM_BOT_TOKEN=123456:ABC...
    TELEGRAM_CHAT_ID=123456789

Telegram 單則上限 4096 字，超過會自動分段（依段落切，不會切在句子中間）。
連線策略：優先 IPv6、失敗退 IPv4（部分網路環境 IPv4 到 Telegram 上行極慢，
圖片會被閘道以 504 拒絕；詳見 _post() 註解）。失敗會重試並寫入 執行紀錄.log。
"""
import sys
import time
import socket
import ssl
import http.client
import urllib.request
import urllib.parse
import urllib.error
import json
import uuid
from pathlib import Path

HOST = "api.telegram.org"

RETRIES = 5  # 網路偶發重置／Telegram 端 5xx 時自動重試
BACKOFF = [15, 30, 60, 120]  # 秒；Telegram 卡住通常要等久一點才會恢復

HERE = Path(__file__).parent
LIMIT = 3900  # 留一點餘裕
LOGFILE = HERE / "執行紀錄.log"


def log(msg: str):
    """把訊息同時印出並寫進執行紀錄.log（排程無人看畫面時靠這個追）"""
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    try:
        with LOGFILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def with_retry(fn, *args, **kwargs):
    last = None
    for i in range(RETRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last = e
            if i == RETRIES - 1:
                break
            wait = BACKOFF[min(i, len(BACKOFF) - 1)]
            print(f"[retry] 第 {i+1}/{RETRIES} 次失敗（{type(e).__name__}: {e}），{wait} 秒後重試…")
            time.sleep(wait)
    raise last


# ── 連線層：優先走 IPv6 ────────────────────────────────────────────────
# 2026-08-03 實測：本機到 api.telegram.org 的 **IPv4 上行只有約 600 B/s**，
# 76 KB 的 K 線圖傳不完，Telegram 閘道 120 秒後直接回 504 Gateway Timeout
# （文字訊息因為封包小所以看不出來，只有推圖會死）。
# 同一台機器走 **IPv6 是 60 KB/s，同一張圖 1.3 秒送完**，故一律優先 IPv6，
# 失敗才退回 IPv4。本機 DNS 不回 AAAA 記錄，改用 Google DoH 查。
_v6_cache = None


def resolve_v6():
    """查 api.telegram.org 的 AAAA（本機 DNS 查不到，走 DoH）；查不到回 None"""
    global _v6_cache
    if _v6_cache is not None:
        return _v6_cache or None
    ip = None
    try:  # 先試本機 DNS（有些網路環境是正常的）
        infos = socket.getaddrinfo(HOST, 443, socket.AF_INET6, socket.SOCK_STREAM)
        ip = infos[0][4][0]
    except Exception:
        try:
            url = f"https://dns.google/resolve?name={HOST}&type=AAAA"
            with urllib.request.urlopen(url, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
            for ans in data.get("Answer", []):
                if ans.get("type") == 28:  # AAAA
                    ip = ans["data"]
                    break
        except Exception:
            ip = None
    _v6_cache = ip or ""
    return ip


def _post(path: str, body: bytes, content_type: str, timeout: int):
    """POST 到 Telegram：先 IPv6、失敗退 IPv4。回傳解析後的 JSON dict"""
    attempts = []
    v6 = resolve_v6()
    if v6:
        attempts.append(v6)
    attempts.append(None)  # None = 交給系統解析（IPv4）
    last = None
    for ip in attempts:
        conn = http.client.HTTPSConnection(HOST, 443, timeout=timeout)
        if ip:  # 指定 IP 連線，但 TLS 仍用網域名做 SNI／憑證驗證
            ctx = ssl.create_default_context()
            sock = socket.create_connection((ip, 443), timeout)
            conn.sock = ctx.wrap_socket(sock, server_hostname=HOST)
        try:
            conn.request("POST", path, body=body, headers={"Content-Type": content_type})
            resp = conn.getresponse()
            raw = resp.read()
            if resp.status != 200:
                raise RuntimeError(f"Telegram HTTP {resp.status}："
                                   f"{raw[:200].decode('utf-8', 'ignore')}")
            return json.loads(raw.decode("utf-8"))
        except Exception as e:
            last = e
            if ip:
                print(f"[net] IPv6 送出失敗（{type(e).__name__}: {e}），改試 IPv4…")
        finally:
            try:
                conn.close()
            except Exception:
                pass
    raise last


def load_env():
    env = {}
    envfile = HERE / ".env"
    if not envfile.exists():
        print("X 找不到 .env，請先建立（內容：TELEGRAM_BOT_TOKEN=... 與 TELEGRAM_CHAT_ID=...）")
        sys.exit(2)
    for line in envfile.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    if "TELEGRAM_BOT_TOKEN" not in env or "TELEGRAM_CHAT_ID" not in env:
        print("X .env 缺 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID")
        sys.exit(2)
    return env


def split_chunks(text: str):
    """依段落切塊，每塊不超過 LIMIT 字"""
    chunks, cur = [], ""
    for para in text.split("\n\n"):
        candidate = (cur + "\n\n" + para) if cur else para
        if len(candidate) <= LIMIT:
            cur = candidate
        else:
            if cur:
                chunks.append(cur)
            # 單一段落本身就超長 → 硬切
            while len(para) > LIMIT:
                chunks.append(para[:LIMIT])
                para = para[LIMIT:]
            cur = para
    if cur:
        chunks.append(cur)
    return chunks


def send(token: str, chat_id: str, text: str):
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    body = _post(f"/bot{token}/sendMessage", data,
                 "application/x-www-form-urlencoded", timeout=60)
    if not body.get("ok"):
        raise RuntimeError(f"Telegram 回傳失敗：{body}")


def send_photo(token: str, chat_id: str, photo_path: Path, caption: str = ""):
    """用 multipart/form-data 上傳一張圖到 Telegram"""
    boundary = uuid.uuid4().hex
    img = photo_path.read_bytes()
    parts = []
    for name, val in [("chat_id", chat_id), ("caption", caption)]:
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{val}\r\n".encode("utf-8"))
    parts.append(
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; "
         f"filename=\"{photo_path.name}\"\r\nContent-Type: image/png\r\n\r\n").encode("utf-8"))
    parts.append(img)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    # 圖片是上行大封包，走 IPv4 會被拖到 Telegram 閘道逾時（見 _post 說明），逾時放寬到 180 秒
    result = _post(f"/bot{token}/sendPhoto", body,
                   f"multipart/form-data; boundary={boundary}", timeout=180)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram sendPhoto 失敗：{result}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    env = load_env()

    if sys.argv[1] == "--photos":
        folder = Path(sys.argv[2]) if len(sys.argv) > 2 else None
        if not folder or not folder.is_dir():
            print(f"X 找不到圖片資料夾：{folder}")
            sys.exit(2)
        pngs = sorted(folder.glob("*.png"))
        if not pngs:
            print("X 資料夾內沒有 png")
            sys.exit(2)
        # 已成功送出的清單：補推時自動跳過，不會重複洗版
        sent_file = folder / ".sent.txt"
        sent = ({ln.strip().lstrip("﻿") for ln in sent_file.read_text(encoding="utf-8").splitlines()}
                if sent_file.exists() else set())
        failed = []
        for i, p in enumerate(pngs, 1):
            if p.name in sent:
                print(f"-- 跳過（先前已送出）{i}/{len(pngs)}：{p.name}")
                continue
            caption = p.stem.split("_", 1)[-1] if "_" in p.stem else p.stem
            try:
                with_retry(send_photo, env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"], p, caption)
            except Exception as e:
                # 單張失敗不中斷整批，其餘照送
                failed.append(p.name)
                log(f"X 圖片推播失敗 {p.name}：{type(e).__name__}: {e}")
                continue
            sent.add(p.name)
            with sent_file.open("a", encoding="utf-8") as f:
                f.write(p.name + "\n")
            print(f"OK 圖片 {i}/{len(pngs)}：{p.name}")
        if failed:
            log(f"X 圖片推播未完成，失敗 {len(failed)}/{len(pngs)} 張：{', '.join(failed)}"
                f"（可稍後重跑同一行指令，成功過的會自動跳過）")
            sys.exit(1)
        print("完成：圖片推播成功")
        return

    if sys.argv[1] == "--text":
        content = " ".join(sys.argv[2:]) or "（測試訊息）"
    else:
        f = Path(sys.argv[1])
        if not f.exists():
            print(f"X 找不到檔案：{f}")
            sys.exit(2)
        content = f.read_text(encoding="utf-8")

    chunks = split_chunks(content)
    for i, chunk in enumerate(chunks, 1):
        try:
            with_retry(send, env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"], chunk)
        except Exception as e:
            log(f"X 文字推播失敗（第 {i}/{len(chunks)} 段）：{type(e).__name__}: {e}")
            sys.exit(1)
        print(f"OK 已送出第 {i}/{len(chunks)} 段（{len(chunk)} 字）")
    print("完成：Telegram 推播成功")


if __name__ == "__main__":
    main()
