# CTFtime → Discord Notifier

Posts upcoming CTFs from [CTFtime](https://ctftime.org) to a Discord channel, once each, on a daily schedule — completely free, running on GitHub Actions.

## How it works
- `ctftime_notifier.py` queries the public CTFtime API, looking back a few days (to catch ongoing events) and forward `NOTIFY_WINDOW_DAYS`.
- Three kinds of Discord posts, each sent **once per event**:
  - **🟢 Upcoming** — posted when a new CTF is first found starting within the window (but more than `REMINDER_MINUTES` away).
  - **⏰ Starting soon** — posted once the event is within `REMINDER_MINUTES` (default 60) of its start time — a heads-up before it begins.
  - **🔴 Live now** — posted the moment the event has actually started (or is caught already running), showing when it ends.
- Each embed includes Format, Weight, Duration, Mode (onsite/online), Restrictions, Organizers, Team size (when listed), Location, links to both the official site and the CTFtime page, and a cleaned-up description with working links preserved (not stripped like raw HTML).
- Already-posted events are tracked separately per tier in `notified_ids.json`, so nothing repeats.
- `.github/workflows/ctf-notify.yml` runs the script **every 30 minutes** and commits the updated state file back to the repo.
- Event times are shown in Bangladesh time (UTC+6).

**Timing note:** the "starting soon" and "live now" alerts are only as precise as how often this runs. At every 30 minutes, you'll get the starting-soon alert somewhere in the last ~60 minutes before kickoff, and the live alert within ~30 minutes of actual start.

**Free tier note:** running every 30 minutes is ~1,400 runs/month, each taking well under a minute. Public repos get unlimited free Actions minutes; private repos get 2,000 free minutes/month, so this comfortably fits either way.

## Setup (5 minutes)

1. **Create a Discord webhook**
   - In Discord: Server Settings → Integrations → Webhooks → New Webhook.
   - Pick the channel you want notifications in, copy the Webhook URL.

2. **Create a GitHub repo** and add these files to it (`ctftime_notifier.py`, `notified_ids.json`, `.github/workflows/ctf-notify.yml`). You can do this via the web UI: "Add file" → "Upload files" for the Python/JSON files, and "Add file" → "Create new file" (naming it `.github/workflows/ctf-notify.yml`) for the workflow.

3. **Add the webhook as a secret**
   - Repo → Settings → Secrets and variables → Actions → New repository secret.
   - Name: `DISCORD_WEBHOOK_URL`
   - Value: (paste the webhook URL from step 1)

4. **Enable Actions** on the repo if prompted (Actions tab → "I understand, enable").

5. **Test it manually** — Actions tab → "CTFtime Discord Notifier" → "Run workflow". Check the run logs and your Discord channel.

6. Done. It'll run automatically every day at 08:00 UTC from then on.

## Customize
- **Notification window**: edit `NOTIFY_WINDOW_DAYS` in the workflow file (default 14 days out).
- **Reminder window**: edit `REMINDER_MINUTES` in the workflow file (default 60 minutes before start).
- **Schedule**: edit the `cron` line in the workflow (currently every 30 minutes: `*/30 * * * *`). Run more often for tighter timing, less often to save minutes.
- **Filter by weight/format**: add a filter in `main()` in `ctftime_notifier.py`, e.g. skip events with `weight` below some threshold.
- **Timezone**: edit the `BDT` offset in `ctftime_notifier.py` if you ever want a different timezone than UTC+6.
- **Lookback window**: `LOOKBACK_DAYS` (default 5) controls how far back the script checks for events that might still be ongoing. Increase it if you follow CTFs that run longer than 5 days.

## Troubleshooting
- **403 from CTFtime or Discord**: both sit behind Cloudflare, which can block requests that look like bots. The script already sends browser-like headers to work around this — if it still fails, test with `curl` directly to see whether it's a header issue or an IP-reputation block (common on shared cloud IPs, including some GitHub Actions runners).
- **403 error code 1010 from Discord specifically**: this is Discord's WAF rejecting the payload content (not the webhook itself). The script sanitizes event titles/descriptions/locations to avoid this, but if it recurs, check what characters are in the offending CTFtime event.

## Run it locally (optional, for testing)
```bash
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python3 ctftime_notifier.py
```

⚠️ Never paste your real webhook URL anywhere public (chat logs, commits, issues) — anyone with it can post to your channel. Regenerate it in Discord if it's ever exposed.
