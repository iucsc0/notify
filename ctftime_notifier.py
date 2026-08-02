#!/usr/bin/env python3
"""
CTFtime -> Discord notifier.

Fetches upcoming CTF events from the CTFtime API and posts any events
starting within NOTIFY_WINDOW_DAYS to a Discord webhook, once each
(tracked via a small JSON state file so we don't spam duplicates).

Env vars:
  DISCORD_WEBHOOK_URL   - required, your Discord webhook URL
  NOTIFY_WINDOW_DAYS    - optional, default 14
  STATE_FILE            - optional, default "notified_ids.json"
"""

import os
import json
import sys
import time
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
import urllib.request
import urllib.error

CTFTIME_API = "https://ctftime.org/api/v1/events/"
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
NOTIFY_WINDOW_DAYS = int(os.environ.get("NOTIFY_WINDOW_DAYS", "14"))
STATE_FILE = Path(os.environ.get("STATE_FILE", "notified_ids.json"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_upcoming_events(limit=30, retries=3):
    now = int(time.time())
    url = f"{CTFTIME_API}?limit={limit}&start={now}"
    req = urllib.request.Request(url, headers=HEADERS)

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            wait = 5 * attempt
            print(f"CTFtime fetch attempt {attempt}/{retries} failed ({e}); retrying in {wait}s...", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"Failed to fetch CTFtime events after {retries} attempts: {last_err}")


def load_state():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_state(ids):
    STATE_FILE.write_text(json.dumps(sorted(ids)))


HTML_TAG_RE = re.compile(r"<[^>]+>")


def sanitize_text(text, max_len=300):
    if not text:
        return ""
    text = HTML_TAG_RE.sub("", text)
    # drop control/non-printable characters that can trip WAF filters
    text = "".join(ch for ch in text if ch.isprintable() or ch in "\n\r\t")
    text = text.strip()
    if len(text) > max_len:
        text = text[:max_len - 1].rstrip() + "…"
    return text


def format_embed(event):
    start = datetime.fromisoformat(event["start"].replace("Z", "+00:00"))
    finish = datetime.fromisoformat(event["finish"].replace("Z", "+00:00"))
    duration_h = round((finish - start).total_seconds() / 3600, 1)

    fields = [
        {"name": "Format", "value": event.get("format", "N/A"), "inline": True},
        {"name": "Weight", "value": str(event.get("weight", "N/A")), "inline": True},
        {"name": "Duration", "value": f"{duration_h}h", "inline": True},
        {"name": "Starts (UTC)", "value": start.strftime("%Y-%m-%d %H:%M"), "inline": False},
    ]
    if event.get("location"):
        fields.append({"name": "Location", "value": sanitize_text(event["location"], max_len=100), "inline": False})

    embed = {
        "title": sanitize_text(event.get("title", "Untitled CTF"), max_len=250) or "Untitled CTF",
        "url": event.get("url") or event.get("ctftime_url"),
        "color": 0x2ECC71,
        "fields": fields,
    }
    desc = sanitize_text(event.get("description"), max_len=300)
    if desc:
        embed["description"] = desc
    if event.get("logo"):
        embed["thumbnail"] = {"url": event["logo"]}
    return embed


def post_to_discord(embeds):
    payload = {"embeds": embeds}
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": HEADERS["User-Agent"],
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"Discord webhook rejected the request (HTTP {e.code}): {body}", file=sys.stderr)
        raise


def main():
    if not WEBHOOK_URL:
        print("ERROR: DISCORD_WEBHOOK_URL is not set.", file=sys.stderr)
        sys.exit(1)

    events = fetch_upcoming_events()
    notified = load_state()
    cutoff = datetime.now(timezone.utc) + timedelta(days=NOTIFY_WINDOW_DAYS)

    new_events = []
    for event in events:
        eid = str(event["id"])
        start = datetime.fromisoformat(event["start"].replace("Z", "+00:00"))
        if eid in notified:
            continue
        if start > cutoff:
            continue
        new_events.append(event)

    if not new_events:
        print("No new upcoming CTFs to notify.")
        return

    # Discord allows up to 10 embeds per message
    for i in range(0, len(new_events), 10):
        batch = new_events[i:i + 10]
        embeds = [format_embed(e) for e in batch]
        post_to_discord(embeds)
        for e in batch:
            notified.add(str(e["id"]))
        print(f"Posted {len(batch)} event(s) to Discord.")

    save_state(notified)


if __name__ == "__main__":
    main()
