#!/usr/bin/env python3
"""Adyru LinkedIn publisher (runs on GitHub Actions).

Posts queue items from publisher/queue/linkedin/*.json to the Adyru LinkedIn company page at
their exact send_at time, through a Make.com webhook scenario (free plan) whose
"Create an Organization Image/Text Post" module posts as the page.

Queue item:
{
  "id": "2026-11-13-li-support-agent",
  "send_at": "2026-11-13T10:30:00+03:30",
  "text": "post text",
  "image": "publisher/media/2026-11-13-li-support-agent.jpg"   # optional (repo path or https URL)
}
Payload sent to the webhook: {"id", "text", "image_url" ("" when none), "has_image"}.
State: publisher/state/linkedin.json (nothing is posted twice).
Env: MAKE_LINKEDIN_WEBHOOK (secret), GITHUB_REPOSITORY, GITHUB_REF_NAME, WINDOW_MINUTES.
"""
import glob, json, os, sys, time, datetime as dt, urllib.request, urllib.error, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE = os.path.join(ROOT, "publisher", "queue", "linkedin")
STATE = os.path.join(ROOT, "publisher", "state", "linkedin.json")
HOOK = os.environ.get("MAKE_LINKEDIN_WEBHOOK", "").strip()
REPO = os.environ.get("GITHUB_REPOSITORY", "muhamadjnq/Adyruos.github.io")
REF = os.environ.get("GITHUB_REF_NAME", "claude/probe")
WINDOW = int(os.environ.get("WINDOW_MINUTES", "14"))
MAX_LATE_HOURS = 6
TEXT_LIMIT = 3000  # LinkedIn post commentary limit


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


def public_url(path):
    if not path:
        return ""
    if path.startswith("http"):
        return path
    return f"https://raw.githubusercontent.com/{REPO}/{urllib.parse.quote(REF)}/{urllib.parse.quote(path)}"


def post(item, retries=3):
    payload = {"id": item["id"], "text": (item.get("text") or "")[:TEXT_LIMIT],
               "image_url": public_url(item.get("image")), "has_image": bool(item.get("image"))}
    if payload["image_url"]:  # make sure the image is live before LinkedIn fetches it
        for _ in range(10):
            try:
                with urllib.request.urlopen(urllib.request.Request(payload["image_url"], method="HEAD"), timeout=20) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(15)
    data = json.dumps(payload).encode()
    for attempt in range(retries):
        req = urllib.request.Request(HOOK, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read().decode(errors="replace")
                try:
                    resp = json.loads(body)
                except Exception:
                    resp = {"raw": body[:300]}
                if isinstance(resp, dict) and resp.get("ok") is False:
                    return False, resp
                return True, resp
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(10 * (attempt + 1)); continue
            return False, {"http": e.code, "body": body}
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(10 * (attempt + 1)); continue
            return False, {"error": str(e.reason)}
    return False, {"error": "retries exhausted"}


def main():
    if not HOOK:
        print("MAKE_LINKEDIN_WEBHOOK secret is not set; nothing posted.")
        return 0
    st = load_state()
    items = []
    for p in sorted(glob.glob(os.path.join(QUEUE, "*.json"))):
        with open(p) as f:
            it = json.load(f)
        if it.get("id") in st["sent"]:
            continue
        try:
            it["_at"] = dt.datetime.fromisoformat(it["send_at"])
        except Exception:
            st["failed"][it.get("id", p)] = {"error": "bad send_at", "at": now().isoformat()}
            continue
        if (now() - it["_at"]).total_seconds() > MAX_LATE_HOURS * 3600:
            st["failed"].setdefault(it["id"], {"error": f"missed: more than {MAX_LATE_HOURS}h late", "at": now().isoformat()})
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
        ok, resp = post(it)
        if ok:
            st["sent"][it["id"]] = {"sent_at": now().isoformat(), "scheduled_for": it["send_at"], "response": resp,
                                    "url": (resp.get("url") if isinstance(resp, dict) else None)}
            st["failed"].pop(it["id"], None)
            print("sent", it["id"], resp)
        else:
            st["failed"][it["id"]] = {"error": json.dumps(resp)[:400], "at": now().isoformat()}
            print("FAILED", it["id"], resp)
        changed = True
        save_state(st)
    if changed or not os.path.exists(STATE):
        save_state(st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
