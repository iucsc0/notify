#!/usr/bin/env python3
"""
CTFtime -> Discord notifier.

Fetches CTF events from the CTFtime API and posts to a Discord webhook in
three ways, each tracked separately so nothing gets posted twice:

  1. UPCOMING      - a new CTF is found starting within NOTIFY_WINDOW_DAYS
                      (posted once, as soon as it's discovered).
  2. STARTING SOON - posted once, when the event is within REMINDER_MINUTES
                      of its start time (a heads-up before it begins).
  3. LIVE NOW       - posted once, the moment the event has actually started
                      (or is caught already running), showing when it ends.

Env vars:
  DISCORD_WEBHOOK_URL   - required, your Discord webhook URL
  NOTIFY_WINDOW_DAYS    - optional, default 14
  REMINDER_MINUTES      - optional, default 60 (heads-up window before start)
  STATE_FILE            - optional, default "notified_ids.json"

Note: precision of "starting soon" / "live now" depends on how often this
script runs (see the GitHub Actions cron schedule). Running every 15-30
minutes gives timely alerts; once a day only catches things in hindsight.
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
NOTIFY_WINDOW_DAYS = int(os.environ.get("NOTIFY_WINDOW_DAYS", "1"))
REMINDER_MINUTES = int(os.environ.get("REMINDER_MINUTES", "60"))
STATE_FILE = Path(os.environ.get("STATE_FILE", "notified_ids.json"))

# How far back to look for events that might still be ongoing right now.
LOOKBACK_DAYS = 5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

BDT = timezone(timedelta(hours=6))

TAG_RE = re.compile(r"<[^>]+>")
TEAM_SIZE_RE = re.compile(r"team\s*size[:\s\[]*([0-9]+\s*-\s*[0-9]+|[0-9]+)", re.IGNORECASE)
DISCORD_RE = re.compile(r'(https?://discord(?:\.gg|\.com/invite)/[^\s"<>]+)', re.IGNORECASE)


def fetch_events(start_ts, limit=60, retries=3):
    url = f"{CTFTIME_API}?limit={limit}&start={start_ts}"
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
    empty = {"upcoming": set(), "soon": set(), "live": set()}
    if not STATE_FILE.exists():
        return empty
    raw = json.loads(STATE_FILE.read_text())
    if isinstance(raw, list):  # oldest format
        return {"upcoming": set(raw), "soon": set(), "live": set()}
    return {
        "upcoming": set(raw.get("upcoming", [])),
        "soon": set(raw.get("soon", [])),
        "live": set(raw.get("live", [])),
    }


def save_state(state):
    STATE_FILE.write_text(json.dumps({k: sorted(v) for k, v in state.items()}))


def sanitize_text(text, max_len=300):
    if not text:
        return ""
    text = TAG_RE.sub("", text)
    text = "".join(ch for ch in text if ch.isprintable() or ch in "\n\r\t")
    text = text.strip()
    if len(text) > max_len:
        text = text[:max_len - 1].rstrip() + "…"
    return text


def parse_dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def format_embed(event, mode):
    """mode is 'upcoming', 'soon', or 'live'."""
    start = parse_dt(event["start"])
    finish = parse_dt(event["finish"])
    duration_h = round((finish - start).total_seconds() / 3600, 1)
    start_bdt = start.astimezone(BDT)
    finish_bdt = finish.astimezone(BDT)

    organizers = ", ".join(o.get("name", "") for o in event.get("organizers", []) if o.get("name"))
    onsite = "Onsite" if event.get("onsite") else "Online"
    restrictions = event.get("restrictions") or "N/A"
    raw_desc = event.get("description") or ""
    team_size_match = TEAM_SIZE_RE.search(raw_desc)
    discord_match = DISCORD_RE.search(raw_desc)

    fields = [
        {"name": "Format", "value": event.get("format", "N/A"), "inline": True},
        {"name": "Weight", "value": str(event.get("weight", "N/A")), "inline": True},
        {"name": "Duration", "value": f"{duration_h}h", "inline": True},
        {"name": "Mode", "value": onsite, "inline": True},
        {"name": "Restrictions", "value": sanitize_text(restrictions, max_len=100) or "N/A", "inline": True},
    ]
    if team_size_match:
        fields.append({"name": "Team size", "value": team_size_match.group(1), "inline": True})
    if organizers:
        fields.append({"name": "Organizers", "value": sanitize_text(organizers, max_len=200), "inline": False})
    if event.get("location"):
        fields.append({"name": "Location", "value": sanitize_text(event["location"], max_len=100), "inline": False})

    official_url = event.get("url")
    ctftime_url = event.get("ctftime_url")
    if official_url and ctftime_url and official_url != ctftime_url:
        fields.append({"name": "Official site", "value": f"[Visit]({official_url})", "inline": True})
    if ctftime_url:
        fields.append({"name": "CTFtime page", "value": f"[Visit]({ctftime_url})", "inline": True})
    if discord_match:
        fields.append({"name": "Discord", "value": f"[Join]({discord_match.group(1)})", "inline": True})
    if mode == "live" and event.get("live_feed"):
        fields.append({"name": "Live scoreboard", "value": f"[Watch]({event['live_feed']})", "inline": True})

    if mode == "live":
        fields.append({"name": "Started (BDT)", "value": start_bdt.strftime("%Y-%m-%d %H:%M"), "inline": True})
        fields.append({"name": "Ends (BDT)", "value": finish_bdt.strftime("%Y-%m-%d %H:%M"), "inline": True})
        title_prefix, color = "🔴 LIVE NOW: ", 0xE74C3C
    elif mode == "soon":
        fields.append({"name": "Starts (BDT)", "value": start_bdt.strftime("%Y-%m-%d %H:%M"), "inline": True})
        fields.append({"name": "Ends (BDT)", "value": finish_bdt.strftime("%Y-%m-%d %H:%M"), "inline": True})
        title_prefix, color = "⏰ STARTING SOON: ", 0xF39C12
    else:
        fields.append({"name": "Starts (BDT)", "value": start_bdt.strftime("%Y-%m-%d %H:%M"), "inline": True})
        fields.append({"name": "Ends (BDT)", "value": finish_bdt.strftime("%Y-%m-%d %H:%M"), "inline": True})
        title_prefix, color = "", 0x2ECC71

    embed = {
        "title": title_prefix + (sanitize_text(event.get("title", "Untitled CTF"), max_len=230) or "Untitled CTF"),
        "url": ctftime_url or official_url,
        "color": color,
        "fields": fields,
    }
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


def post_batches(events, mode, state):
    if not events:
        return
    for i in range(0, len(events), 10):
        batch = events[i:i + 10]
        embeds = [format_embed(e, mode) for e in batch]
        post_to_discord(embeds)
        for e in batch:
            state[mode].add(str(e["id"]))
        print(f"Posted {len(batch)} '{mode}' event(s) to Discord.")


def main():
    if not WEBHOOK_URL:
        print("ERROR: DISCORD_WEBHOOK_URL is not set.", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc)
    lookback_ts = int((now - timedelta(days=LOOKBACK_DAYS)).timestamp())
    events = fetch_events(lookback_ts)

    state = load_state()
    cutoff = now + timedelta(days=NOTIFY_WINDOW_DAYS)
    reminder_cutoff = now + timedelta(minutes=REMINDER_MINUTES)

    live_events, soon_events, upcoming_events = [], [], []

    for event in events:
        eid = str(event["id"])
        start = parse_dt(event["start"])
        finish = parse_dt(event["finish"])

        if start <= now < finish:
            if eid not in state["live"]:
                live_events.append(event)
        elif now < start <= reminder_cutoff:
            if eid not in state["soon"]:
                soon_events.append(event)
            if eid not in state["upcoming"]:
                state["upcoming"].add(eid)  # don't double-post as "upcoming" too
        elif now < start <= cutoff:
            if eid not in state["upcoming"]:
                upcoming_events.append(event)

    if not (live_events or soon_events or upcoming_events):
        print("Nothing new to notify.")
        return

    post_batches(live_events, "live", state)
    post_batches(soon_events, "soon", state)
    post_batches(upcoming_events, "upcoming", state)
    save_state(state)


if __name__ == "__main__":
    main()
