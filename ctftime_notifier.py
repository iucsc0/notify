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
from datetime import datetime, timezone, timedelta
from pathlib import Path
import urllib.request

CTFTIME_API = "https://ctftime.org/api/v1/events/"
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
NOTIFY_WINDOW_DAYS = int(os.environ.get("NOTIFY_WINDOW_DAYS", "14"))
STATE_FILE = Path(os.environ.get("STATE_FILE", "notified_ids.json"))

HEADERS = {"User-Agent": "ctftime-discord-notifier/1.0"}


def fetch_upcoming_events(limit=30):
    now = int(time.time())
    url = f"{CTFTIME_API}?limit={limit}&start={now}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def load_state():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_state(ids):
    STATE_FILE.write_text(json.dumps(sorted(ids)))


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
        fields.append({"name": "Location", "value": event["location"], "inline": False})

    return {
        "title": event.get("title", "Untitled CTF"),
        "url": event.get("url") or event.get("ctftime_url"),
        "description": (event.get("description") or "")[:300],
        "color": 0x2ECC71,
        "fields": fields,
        "thumbnail": {"url": event["logo"]} if event.get("logo") else None,
    }


def post_to_discord(embeds):
    payload = {"embeds": embeds}
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status


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
