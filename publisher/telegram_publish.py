#!/usr/bin/env python3
"""Adyru Telegram publisher (runs on GitHub Actions).

Reads queue items from publisher/queue/telegram/*.json and posts each one to the
Telegram channel through the official Bot API at its exact send_at time.

Queue item (one JSON file per post):
{
  "id": "2026-11-13-tg-seo",              # unique, never reused
  "send_at": "2026-11-13T11:00:00+03:30",  # ISO 8601 with offset
  "text": "caption or message text",
  "photo": "publisher/media/2026-11-13-tg-seo.jpg",   # optional, repo path (or https URL)
  "chat_id": "@adyru"                      # optional, overrides TELEGRAM_CHAT_ID
}
State of sent items is kept in publisher/state/telegram.json so nothing is posted twice.

Env: TELEGRAM_BOT_TOKEN (secret), TELEGRAM_CHAT_ID (secret or variable),
     WINDOW_MINUTES (how far ahead this run waits for items, default 14).
"""
import glob, json, os, sys, time, datetime as dt, urllib.request, urllib.error, uuid, mimetypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE = os.path.join(ROOT, "publisher", "queue", "telegram")
STATE = os.path.join(ROOT, "publisher", "state", "telegram.json")
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
DEFAULT_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
WINDOW = int(os.environ.get("WINDOW_MINUTES", "14"))
CAPTION_LIMIT, TEXT_LIMIT = 1024, 4096
DRY = os.environ.get("DRY_RUN") == "1"
API_BASE = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")
MAX_LATE_HOURS = 6  # an item more than 6h late is not posted (marked missed) so stale posts never go out


def now():
    return dt.datetime.now(dt.timezone.utc)


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"sent": {}, "failed": {}, "checked_at": None}


def save_state(s):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    s["checked_at"] = now().isoformat()
    with open(STATE, "w") as f:
        json.dump(s, f, indent=2, ensure_ascii=False, sort_keys=True)


def api(method, fields=None, files=None, retries=4):
    url = f"{API_BASE}/bot{TOKEN}/{method}"
    for attempt in range(retries):
        if files:
            boundary = uuid.uuid4().hex
            body = b""
            for k, v in (fields or {}).items():
                body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
            for k, path in files.items():
                ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
                with open(path, "rb") as fh:
                    data = fh.read()
                body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{os.path.basename(path)}\"\r\n"
                         f"Content-Type: {ctype}\r\n\r\n").encode() + data + b"\r\n"
            body += f"--{boundary}--\r\n".encode()
            req = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        else:
            req = urllib.request.Request(url, data=json.dumps(fields or {}).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            payload = json.loads(e.read().decode() or "{}")
            wait = (payload.get("parameters") or {}).get("retry_after")
            if e.code == 429 and wait:
                time.sleep(int(wait) + 1)
                continue
            if e.code >= 500 and attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            return payload
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            raise
    return {"ok": False, "description": "retries exhausted"}


def link_for(chat, msg_id):
    if isinstance(chat, str) and chat.startswith("@"):
        return f"https://t.me/{chat[1:]}/{msg_id}"
    return None


def send(item):
    chat = item.get("chat_id") or DEFAULT_CHAT
    text = (item.get("text") or "").strip()
    photo = item.get("photo")
    base = {"chat_id": chat, "disable_web_page_preview": "false"}
    if photo:
        cap, rest = text, ""
        if len(text) > CAPTION_LIMIT:  # long text: photo first, full text as a reply right under it
            cap, rest = "", text
        fields = {"chat_id": chat}
        if cap:
            fields["caption"] = cap
        if photo.startswith("http"):
            res = api("sendPhoto", {**fields, "photo": photo})
        else:
            res = api("sendPhoto", fields, files={"photo": os.path.join(ROOT, photo)})
        if res.get("ok") and rest:
            mid = res["result"]["message_id"]
            r2 = api("sendMessage", {"chat_id": chat, "text": rest[:TEXT_LIMIT], "reply_to_message_id": mid})
            if not r2.get("ok"):
                return r2
        return res
    return api("sendMessage", {**base, "text": text[:TEXT_LIMIT]})


def main():
    if not TOKEN and not DRY:
        print("TELEGRAM_BOT_TOKEN secret is not set; nothing posted.")
        return 0
    if TOKEN and not DRY:
        me = api("getMe")
        if not me.get("ok"):
            print("Bot token rejected by Telegram:", me.get("description"))
            return 1
    st = load_state()
    items = []
    for p in sorted(glob.glob(os.path.join(QUEUE, "*.json"))):
        with open(p) as f:
            it = json.load(f)
        it["_file"] = os.path.relpath(p, ROOT)
        if it.get("id") in st["sent"]:
            continue
        try:
            it["_at"] = dt.datetime.fromisoformat(it["send_at"])
        except Exception:
            st["failed"][it.get("id", p)] = {"error": "bad send_at", "at": now().isoformat()}
            continue
        if (now() - it["_at"]).total_seconds() > MAX_LATE_HOURS * 3600:
            if it.get("id") not in st["failed"]:
                st["failed"][it["id"]] = {"error": f"missed: more than {MAX_LATE_HOURS}h late", "at": now().isoformat()}
            continue
        items.append(it)
    horizon = now() + dt.timedelta(minutes=WINDOW)
    due = sorted([i for i in items if i["_at"] <= horizon], key=lambda i: i["_at"])
    print(f"{len(items)} unsent in queue, {len(due)} due by {horizon.isoformat()}")
    changed = False
    for it in due:
        wait = (it["_at"] - now()).total_seconds()
        if wait > 0:
            print(f"waiting {int(wait)}s for {it['id']}")
            time.sleep(wait)
        if DRY:
            print("DRY RUN would send", it["id"]); continue
        res = send(it)
        if res.get("ok"):
            msg = res["result"]
            chat = it.get("chat_id") or DEFAULT_CHAT
            st["sent"][it["id"]] = {"message_id": msg["message_id"], "sent_at": now().isoformat(),
                                    "scheduled_for": it["send_at"], "url": link_for(chat, msg["message_id"]) or (
                                        f"https://t.me/{msg['chat'].get('username')}/{msg['message_id']}" if msg.get("chat", {}).get("username") else None)}
            st["failed"].pop(it["id"], None)
            print("sent", it["id"], st["sent"][it["id"]]["url"])
        else:
            st["failed"][it["id"]] = {"error": res.get("description", "unknown"), "at": now().isoformat()}
            print("FAILED", it["id"], res.get("description"))
        changed = True
        save_state(st)  # saved after every post, so a crash mid-run never causes a repeat
    if changed or not os.path.exists(STATE):
        save_state(st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
