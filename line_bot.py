"""
排程曆 — LINE Bot 伺服器

在 LINE 打一句「報告 8/20 6h」就新增任務，伺服器重新排程後回覆你接下來幾天要做什麼；
網頁端每分鐘拉一次同步。每天早上固定推播當日工作。

啟動：
    pip install flask requests
    set LINE_CHANNEL_SECRET=...        (PowerShell: $env:LINE_CHANNEL_SECRET="...")
    set LINE_CHANNEL_TOKEN=...
    set SYNC_TOKEN=自己取一組密碼
    python line_bot.py

詳細架設步驟見 README.md。
"""

import os
import re
import json
import hmac
import base64
import hashlib
import threading
import time as _time
from datetime import date, datetime, timedelta

import requests
from flask import Flask, request, jsonify, abort

# ----------------------------------------------------------------- 設定
CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
CHANNEL_TOKEN = os.environ.get("LINE_CHANNEL_TOKEN", "")
SYNC_TOKEN = os.environ.get("SYNC_TOKEN", "")
# 只有這些 LINE 使用者可以操作。留空的話採「先到先得」：
# 第一個跟 Bot 說話的人成為擁有者，之後其他人一律擋掉。
ALLOWED_USER_IDS = [s.strip() for s in os.environ.get("ALLOWED_USER_IDS", "").split(",") if s.strip()]
STORE_PATH = os.environ.get("STORE_PATH", "store.json")
PUSH_HOUR = int(os.environ.get("PUSH_HOUR", "8"))  # 每天幾點推播當日工作；設 -1 關閉
PORT = int(os.environ.get("PORT", "8000"))

DEFAULT_SETTINGS = {
    "dailyCapacity": 4,
    "maxChunkPerTask": 2.5,
    "workdays": [1, 2, 3, 4, 5],   # 0=週日 … 6=週六，與網頁端同編號
    "bufferDays": 1,
    "mode": "balanced",
    "holidays": [],
}

app = Flask(__name__)
_lock = threading.Lock()


# ----------------------------------------------------------------- 儲存
def load_store():
    try:
        with open(STORE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data.setdefault("tasks", [])
    data.setdefault("users", [])
    data.setdefault("owner", None)
    s = dict(DEFAULT_SETTINGS)
    s.update(data.get("settings") or {})
    data["settings"] = s
    return data


def save_store(data):
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STORE_PATH)


def new_id():
    return base64.b32encode(os.urandom(5)).decode().lower()[:8]


# ----------------------------------------------------------------- 日期工具
def fmt(d):
    return d.isoformat()


def parse_d(s):
    return date.fromisoformat(s)


def remaining(t):
    return max(0.0, round(float(t.get("hours", 0)) - float(t.get("doneHours", 0)), 2))


def up_half(v):
    return -(-v // 0.5) * 0.5


def dn_half(v):
    return (v // 0.5) * 0.5


def hrs(v):
    v = round(float(v), 1)
    return str(int(v)) if v == int(v) else str(v)


def is_workday(d, st):
    # workdays 用 JavaScript Date.getDay() 的編號（0=週日 … 6=週六），
    # 這樣網頁改了工作日之後同步過來才對得起來。
    return (d.isoweekday() % 7) in st["workdays"] and fmt(d) not in st["holidays"]


# ----------------------------------------------------------------- 排程
def schedule(tasks, settings):
    """回傳 (plan, diag)。

    每天挑「密度最高」的任務先做：密度 = 剩餘時數 ÷ 到期前還剩幾個工作天。
    與網頁端 index.html 的 schedule() 為同一套邏輯，兩邊結果必須一致。
    """
    st = settings
    t0 = date.today()
    active = [{"ref": t, "rem": remaining(t)}
              for t in tasks if not t.get("done") and remaining(t) > 0.01]

    plan = {}
    diag = {t["id"]: {"finish": None,
                      "status": "done" if t.get("done") else "ok",
                      "remaining": remaining(t)} for t in tasks}
    if not active:
        return plan, diag

    last = max([parse_d(a["ref"]["deadline"]) for a in active] + [t0])
    end = last + timedelta(days=120)

    days = []
    d = t0
    while d <= end:
        if is_workday(d, st):
            days.append(fmt(d))
        d += timedelta(days=1)
    if not days:
        return plan, diag

    def last_idx_on_or_before(s):
        i = len(days) - 1
        while i >= 0 and days[i] > s:
            i -= 1
        return i

    def first_idx_on_or_after(s):
        i = 0
        while i < len(days) and days[i] < s:
            i += 1
        return i

    for a in active:
        soft = fmt(parse_d(a["ref"]["deadline"]) - timedelta(days=st["bufferDays"]))
        a["dueIdx"] = last_idx_on_or_before(soft)
        a["hardIdx"] = last_idx_on_or_before(a["ref"]["deadline"])
        a["fromIdx"] = first_idx_on_or_after(a["ref"]["start"]) if a["ref"].get("start") else 0

    for i, key in enumerate(days):
        if not any(a["rem"] > 0.01 for a in active):
            break
        cap = float(st["dailyCapacity"])

        pool = [a for a in active if a["rem"] > 0.01 and a["fromIdx"] <= i]
        for a in pool:
            a["_left"] = max(1, a["dueIdx"] - i + 1)
            a["_den"] = a["rem"] / a["_left"]
        pool.sort(key=lambda a: (-a["_den"], a["ref"]["deadline"], -a["ref"].get("priority", 2)))

        for a in pool:
            if cap <= 0.01:
                break
            want = min(a["rem"], up_half(a["_den"])) if st["mode"] == "balanced" else a["rem"]
            want = min(want, cap, float(st["maxChunkPerTask"]))
            want = dn_half(want)
            if want < 0.5:
                want = min(0.5, a["rem"], cap)
            if want <= 0.01:
                continue
            plan.setdefault(key, []).append({"id": a["ref"]["id"], "hours": round(want, 2)})
            a["rem"] = round(a["rem"] - want, 2)
            cap = round(cap - want, 2)

    for a in active:
        d = diag[a["ref"]["id"]]
        d["remaining"] = a["rem"]
        if a["rem"] > 0.01:
            d["status"] = "unfit"
            continue
        fin = None
        for k, blocks in plan.items():
            if any(b["id"] == a["ref"]["id"] for b in blocks) and (fin is None or k > fin):
                fin = k
        d["finish"] = fin
        if fin is None:
            d["status"] = "ok"
        elif fin > a["ref"]["deadline"]:
            d["status"] = "late"
        elif a["dueIdx"] >= 0 and fin > days[max(0, a["dueIdx"])]:
            d["status"] = "tight"
        else:
            d["status"] = "ok"

    return plan, diag


# ----------------------------------------------------------------- 訊息解析
DOW_TW = "一二三四五六日"
REL_WORDS = {"今天": 0, "今日": 0, "明天": 1, "明日": 1, "後天": 2, "大後天": 3}


def parse_date_token(tok, today_=None):
    """認得 8/20、2026-08-20、8月20日、明天、下週五、+5。認不出來回 None。"""
    t0 = today_ or date.today()
    tok = tok.strip()

    if tok in REL_WORDS:
        return t0 + timedelta(days=REL_WORDS[tok])

    m = re.fullmatch(r"\+(\d+)\s*天?", tok)
    if m:
        return t0 + timedelta(days=int(m.group(1)))

    m = re.fullmatch(r"(這|本|下)\s*(週|周|星期|禮拜)\s*([一二三四五六日天])", tok)
    if m:
        ch = m.group(3).replace("天", "日")
        target = DOW_TW.index(ch) + 1
        delta = (target - t0.isoweekday()) % 7
        if m.group(1) == "下":
            delta += 7
        elif delta == 0:
            delta = 7
        return t0 + timedelta(days=delta)

    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", tok)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    m = re.fullmatch(r"(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*[日號]?", tok)
    if m:
        mo, dy = int(m.group(1)), int(m.group(2))
        if not (1 <= mo <= 12 and 1 <= dy <= 31):
            return None
        y = t0.year
        try:
            d = date(y, mo, dy)
        except ValueError:
            return None
        if d < t0:                      # 已經過了就當作明年
            d = date(y + 1, mo, dy)
        return d

    return None


def parse_hours_token(tok):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(h|hr|hrs|小時|時|鐘頭)?", tok, re.I)
    if not m:
        return None
    if not m.group(2) and "." not in m.group(1) and len(m.group(1)) > 2:
        return None                     # 像 2026 這種純數字不當作時數
    return float(m.group(1))


def parse_new_task(text):
    """把『文獻回顧 8/20 6h』拆成 (名稱, 截止日, 時數)。缺任一項回 None。"""
    parts = text.split()
    if len(parts) < 2:
        return None
    d = h = None
    name_parts = []
    for p in parts:
        if d is None and parse_date_token(p):
            d = parse_date_token(p)
            continue
        if h is None and parse_hours_token(p) is not None:
            h = parse_hours_token(p)
            continue
        name_parts.append(p)
    name = " ".join(name_parts).strip()
    if not name or d is None:
        return None
    return name, d, (h if h else 2.0)


def find_task(tasks, name):
    name = name.strip()
    if not name:
        return None
    open_first = sorted(tasks, key=lambda t: (bool(t.get("done")), t["deadline"]))
    for t in open_first:
        if t["title"] == name:
            return t
    for t in open_first:
        if name in t["title"] or t["title"] in name:
            return t
    return None


# ----------------------------------------------------------------- 回覆文字
HELP = """排程曆 · 可以這樣跟我說

新增　文獻回顧 8/20 6h
　　　（名稱＋何時完成＋要多久，順序隨意）
　　　日期也吃「明天」「下週五」「+10」

今天　今天要做什麼
本週　這週的安排
清單　所有任務和進度
完成 文獻回顧 2h　　記錄做了多久
完成 文獻回顧　　　整件事做完
刪除 文獻回顧
設定 每日 5h　　　調整每天可投入時數"""


def fmt_day_plan(store, key, plan, header):
    blocks = plan.get(key, [])
    if not blocks:
        d = parse_d(key)
        if not is_workday(d, store["settings"]):
            return "%s是週%s，不是工作日，好好休息。\n\n%s" % (
                header, DOW_TW[d.isoweekday() - 1], fmt_next(store, plan))
        return header + "沒有排定的工作。"
    by_id = {t["id"]: t for t in store["tasks"]}
    lines = [header.rstrip("，")]
    total = 0.0
    for b in blocks:
        t = by_id.get(b["id"])
        if not t:
            continue
        total += b["hours"]
        lines.append("・%s　%sh" % (t["title"], hrs(b["hours"])))
    lines.append("共 %sh / 可用 %sh" % (hrs(total), hrs(store["settings"]["dailyCapacity"])))
    return "\n".join(lines)


def fmt_next(store, plan, days=3):
    """接下來幾個有排到工作的日子。今天不是工作日時特別有用。"""
    keys = sorted(k for k in plan if k >= fmt(date.today()))[:days]
    if not keys:
        return "接下來沒有排定的工作。"
    by_id = {t["id"]: t for t in store["tasks"]}
    out = ["接下來"]
    for k in keys:
        d = parse_d(k)
        out.append("%d/%d（%s）%s" % (
            d.month, d.day, DOW_TW[d.isoweekday() - 1],
            "　".join("%s %sh" % (by_id[b["id"]]["title"], hrs(b["hours"]))
                      for b in plan[k] if b["id"] in by_id)))
    return "\n".join(out)


def fmt_week(store, plan):
    t0 = date.today()
    mon = t0 - timedelta(days=t0.isoweekday() - 1)
    by_id = {t["id"]: t for t in store["tasks"]}
    out = ["本週安排"]
    for i in range(7):
        d = mon + timedelta(days=i)
        blocks = plan.get(fmt(d), [])
        if not blocks:
            continue
        mark = "▸" if d == t0 else "　"
        out.append("%s%d/%d（%s）" % (mark, d.month, d.day, DOW_TW[d.isoweekday() - 1]))
        for b in blocks:
            t = by_id.get(b["id"])
            if t:
                out.append("　　%s %sh" % (t["title"], hrs(b["hours"])))
    return "\n".join(out) if len(out) > 1 else "本週沒有排定的工作。"


STATUS_TW = {"ok": "從容", "tight": "緊繃", "late": "會遲交", "unfit": "排不進", "done": "已完成"}


def fmt_list(store, diag):
    tasks = sorted(store["tasks"], key=lambda t: (bool(t.get("done")), t["deadline"]))
    if not tasks:
        return "還沒有任何任務。試試：報告 8/20 6h"
    out = ["任務清單"]
    for t in tasks:
        d = diag.get(t["id"], {})
        st = STATUS_TW.get(d.get("status"), "")
        out.append("・%s　截止 %s" % (t["title"], t["deadline"][5:]))
        out.append("　　%s/%sh・%s%s" % (
            hrs(t.get("doneHours", 0)), hrs(t["hours"]), st,
            "・完工 " + d["finish"][5:] if d.get("finish") else ""))
    return "\n".join(out)


def fmt_after_change(store, plan, diag, task, lead):
    d = diag.get(task["id"], {})
    lines = [lead]
    if d.get("status") == "unfit":
        lines.append("⚠ 排不進去 — 你的工作日已經被更急的事填滿。請延後截止日或調高每日時數。")
    elif d.get("status") == "late":
        lines.append("⚠ 依現在的容量要做到 %s，比截止日晚。" % d["finish"])
    elif d.get("finish"):
        lines.append("預計 %s 完成（截止 %s）" % (d["finish"], task["deadline"]))
    lines.append("")
    lines.append(fmt_day_plan(store, fmt(date.today()), plan, "今天"))
    return "\n".join(lines)


# ----------------------------------------------------------------- 指令處理
def handle_text(text):
    text = (text or "").strip()
    if not text:
        return HELP

    with _lock:
        store = load_store()
        st = store["settings"]
        low = text.lower()
        cmd, _, rest = text.partition(" ")
        rest = rest.strip()

        if low in ("help", "說明", "指令", "?", "？"):
            return HELP

        if cmd in ("完成", "做完", "done"):
            body = rest
            h = None
            parts = body.split()
            if parts and parse_hours_token(parts[-1]) is not None:
                h = parse_hours_token(parts[-1])
                body = " ".join(parts[:-1])
            t = find_task(store["tasks"], body)
            if not t:
                return "找不到「%s」。打「清單」看看目前有哪些任務。" % body
            if h is None:
                t["doneHours"] = t["hours"]
                t["done"] = True
                lead = "「%s」已完成 ✓" % t["title"]
            else:
                t["doneHours"] = round(min(t["hours"], float(t.get("doneHours", 0)) + h), 2)
                if remaining(t) <= 0.01:
                    t["done"] = True
                    lead = "「%s」已完成 ✓" % t["title"]
                else:
                    lead = "「%s」記錄 %sh，還剩 %sh" % (t["title"], hrs(h), hrs(remaining(t)))
            save_store(store)
            plan, diag = schedule(store["tasks"], st)
            return fmt_after_change(store, plan, diag, t, lead)

        if cmd in ("刪除", "移除", "delete"):
            t = find_task(store["tasks"], rest)
            if not t:
                return "找不到「%s」。" % rest
            store["tasks"] = [x for x in store["tasks"] if x["id"] != t["id"]]
            save_store(store)
            plan, _ = schedule(store["tasks"], st)
            return "已刪除「%s」。\n\n%s" % (
                t["title"], fmt_day_plan(store, fmt(date.today()), plan, "今天"))

        if cmd in ("設定", "settings"):
            m = re.search(r"(每日|每天)\s*(\d+(?:\.\d+)?)", rest)
            if m:
                st["dailyCapacity"] = float(m.group(2))
                save_store(store)
                return "每天可投入時數改成 %sh。" % hrs(st["dailyCapacity"])
            return "目前每天可投入 %sh，工作日為週%s。\n要調整請打：設定 每日 5h" % (
                hrs(st["dailyCapacity"]),
                "、週".join(DOW_TW[w - 1] for w in sorted(st["workdays"])))

        if text in ("今天", "今日", "today"):
            plan, _ = schedule(store["tasks"], st)
            return fmt_day_plan(store, fmt(date.today()), plan, "今天")

        if text in ("明天", "明日"):
            plan, _ = schedule(store["tasks"], st)
            return fmt_day_plan(store, fmt(date.today() + timedelta(days=1)), plan, "明天")

        if text in ("本週", "這週", "本周", "這周", "week"):
            plan, _ = schedule(store["tasks"], st)
            return fmt_week(store, plan)

        if text in ("清單", "任務", "list", "全部"):
            plan, diag = schedule(store["tasks"], st)
            return fmt_list(store, diag)

        # 其餘一律當成新增任務
        body = rest if cmd in ("新增", "加", "add") else text
        parsed = parse_new_task(body)
        if not parsed:
            return "看不懂「%s」。\n\n%s" % (text, HELP)
        name, due, h = parsed
        task = {
            "id": new_id(), "title": name, "deadline": fmt(due), "hours": h,
            "doneHours": 0, "priority": 2, "start": fmt(date.today()), "done": False,
        }
        store["tasks"].append(task)
        save_store(store)
        plan, diag = schedule(store["tasks"], st)
        return fmt_after_change(
            store, plan, diag, task,
            "已加入「%s」　截止 %s・預估 %sh" % (name, fmt(due), hrs(h)))


# ----------------------------------------------------------------- LINE API
def line_post(path, payload):
    if not CHANNEL_TOKEN:
        app.logger.warning("LINE_CHANNEL_TOKEN 未設定，略過送出")
        return
    try:
        r = requests.post(
            "https://api.line.me/v2/bot/message/" + path,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + CHANNEL_TOKEN},
            data=json.dumps(payload), timeout=10)
        if r.status_code >= 300:
            app.logger.error("LINE %s 失敗 %s %s", path, r.status_code, r.text[:300])
    except requests.RequestException as e:
        app.logger.error("LINE %s 連線失敗：%s", path, e)


def reply(token, text):
    line_post("reply", {"replyToken": token, "messages": [{"type": "text", "text": text[:4900]}]})


def push(user_id, text):
    line_post("push", {"to": user_id, "messages": [{"type": "text", "text": text[:4900]}]})


@app.post("/callback")
def callback():
    sig = request.headers.get("X-Line-Signature", "")
    body = request.get_data()
    if CHANNEL_SECRET:
        mac = hmac.new(CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
        if not hmac.compare_digest(base64.b64encode(mac).decode(), sig):
            abort(400, "簽章驗證失敗")

    try:
        events = json.loads(body).get("events", [])
    except ValueError:
        abort(400, "內容不是合法的 JSON")

    for ev in events:
        src = ev.get("source", {})
        uid = src.get("userId")

        if not is_authorised(uid):
            app.logger.warning("擋下未授權的使用者：%s", uid)
            if ev.get("replyToken"):
                reply(ev["replyToken"], "這是私人的排程助理，沒有開放給其他人使用。")
            continue

        if ev.get("type") == "follow" and ev.get("replyToken"):
            reply(ev["replyToken"], "已連上排程曆 👋\n\n" + HELP)
        elif ev.get("type") == "message" and ev.get("message", {}).get("type") == "text":
            reply(ev["replyToken"], handle_text(ev["message"]["text"]))
    return "OK"


def is_authorised(uid):
    """只有擁有者能操作 —— 任何人都加得到 LINE 官方帳號，不能讓別人讀寫你的任務。"""
    if not uid:
        return False
    if ALLOWED_USER_IDS:
        return uid in ALLOWED_USER_IDS
    with _lock:
        store = load_store()
        if store["owner"] is None:                  # 先到先得：第一個說話的人就是擁有者
            store["owner"] = uid
            if uid not in store["users"]:
                store["users"].append(uid)
            save_store(store)
            app.logger.warning(
                "已將 %s 記為擁有者。若要鎖定，請把它設進 ALLOWED_USER_IDS 環境變數。", uid)
            return True
        return uid == store["owner"]


# ----------------------------------------------------------------- 網頁同步 API
def check_token():
    """沒設密鑰就直接關閉同步 API —— 不能因為忘了設定就把整份任務清單全開。"""
    if not SYNC_TOKEN:
        abort(503, "尚未設定 SYNC_TOKEN 環境變數，同步 API 已停用。")
    # 轉 bytes 再比，密鑰含中文時 compare_digest 吃 str 會丟 TypeError
    got = request.headers.get("X-Sync-Token", "").encode("utf-8")
    if not hmac.compare_digest(got, SYNC_TOKEN.encode("utf-8")):
        abort(401)


@app.after_request
def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Sync-Token"
    resp.headers["Access-Control-Allow-Methods"] = "GET, PUT, POST, OPTIONS"
    resp.headers["Access-Control-Max-Age"] = "86400"
    return resp


@app.route("/api/<path:_any>", methods=["OPTIONS"])
def preflight(_any):
    return ("", 204)


@app.get("/api/ping")
def ping():
    check_token()
    store = load_store()
    return jsonify(ok=True, tasks=len(store["tasks"]), users=len(store["users"]))


@app.get("/api/state")
def get_state():
    check_token()
    store = load_store()
    return jsonify(tasks=store["tasks"], settings=store["settings"])


@app.put("/api/state")
def put_state():
    check_token()
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get("tasks"), list):
        return jsonify(error="tasks 必須是陣列"), 400
    with _lock:
        store = load_store()
        store["tasks"] = data["tasks"]
        if isinstance(data.get("settings"), dict):
            store["settings"].update(data["settings"])
        save_store(store)
    return jsonify(ok=True, tasks=len(store["tasks"]))


@app.get("/")
def health():
    return "排程曆 LINE Bot 運作中"


# ----------------------------------------------------------------- 每日推播
def push_loop():
    sent_on = None
    while True:
        try:
            now = datetime.now()
            if PUSH_HOUR >= 0 and now.hour == PUSH_HOUR and sent_on != now.date():
                sent_on = now.date()
                store = load_store()
                # 只推給擁有者：store["users"] 可能混進別人，推過去等於外洩整份任務清單
                targets = ALLOWED_USER_IDS or ([store["owner"]] if store["owner"] else [])
                if targets:
                    plan, diag = schedule(store["tasks"], store["settings"])
                    msg = "早安 ☀\n" + fmt_day_plan(store, fmt(date.today()), plan, "今天")
                    risky = [t for t in store["tasks"]
                             if diag.get(t["id"], {}).get("status") in ("late", "unfit")]
                    if risky:
                        msg += "\n\n⚠ 有風險：" + "、".join(t["title"] for t in risky)
                    for uid in targets:
                        push(uid, msg)
        except Exception as e:                       # 推播失敗不能弄掛伺服器
            app.logger.error("每日推播失敗：%s", e)
        _time.sleep(60)


if __name__ == "__main__":
    if not SYNC_TOKEN:
        print("⚠ 未設定 SYNC_TOKEN，網頁同步 API 已停用（LINE 功能不受影響）。")
    if not ALLOWED_USER_IDS:
        _o = load_store()["owner"]
        print("⚠ 未設定 ALLOWED_USER_IDS，採先到先得：%s" %
              ("目前擁有者 " + _o if _o else "第一個跟 Bot 說話的人將成為擁有者"))
    if PUSH_HOUR >= 0:
        threading.Thread(target=push_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
