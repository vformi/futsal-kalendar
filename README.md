# Futsal Plzeň calendar

Turns a team's schedule on [futsalvplzni.cz](https://futsalvplzni.cz/rozpis) into an
`.ics` feed. Subscribe once; postponements arrive on their own.

**Spitfire:** `https://vformi.github.io/futsal-kalendar/spitfire.ics`

- **Google Calendar** — Other calendars → + → From URL
- **Apple Calendar** — File → New Calendar Subscription → set *Auto-refresh* to
  something other than the weekly default
- **Outlook** — Add calendar → Subscribe from web

## Run it for your own team

Works for any of the 72 teams the schedule page lists, not just Spitfire.

1. Fork this repo.
2. In `.github/workflows/update-calendar.yml`, set `FUTSAL_TEAM` to your team and
   `OUT` to the filename you want. The name is matched as a substring, ignoring
   case and diacritics, so `Legion` finds `FC Legion`.
3. Settings → Pages → deploy from `main`, folder `/`.
4. Actions → Update calendar → Run workflow.

Your feed is then at `https://<you>.github.io/<repo>/<OUT>`.

Check it locally first:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run.py --team "FC Legion"            # print the schedule
.venv/bin/python run.py --team "FC Legion" --ics --out legion.ics
```

## How it stays current

A GitHub Action runs **Wednesdays and Fridays at 05:00 UTC** and commits only when
the schedule actually changed. Subscribers re-fetch on their own client's interval —
GitHub Pages caches for 10 minutes, and Google is typically hours slower than that.

GitHub disables scheduled workflows after ~60 days of repo inactivity. The job's own
commits count, so this holds during a season; expect to re-enable it after the break.

## What it gets right

**Postponements update in place.** Event UIDs come from the fixture's identity —
competition, home, away — never its kickoff time. A postponed match keeps its UID,
so clients move the existing event and bump `SEQUENCE` instead of deleting one event
and adding an unrelated new one.

**Played matches stay.** The site lists upcoming fixtures only. Events whose kickoff
has passed are carried over from the published file, so a season's history doesn't
drain out of subscribers' calendars a week at a time. A fixture that disappears while
still in the future is treated as cancelled and dropped.

**Times can't drift.** Events are written as UTC instants rather than `TZID`
references, so no `VTIMEZONE` component is needed and clients without a timezone
database can't shift them.

**A broken scrape can't wipe your calendar.** Because played fixtures are retained,
the event count never falls on its own. If it does — the site changed its markup and
the parser returned a partial schedule — the job fails instead of committing. Genuine
losses go through a manual run with `allow_shrink`.

## Testing that updates land

Verifying a postponement needs a change to react to, and the league only provides those
on its own schedule. `tools/simulate_change.py` stages one, writing exactly what
`run.py` would: same UID, new `SEQUENCE`, `LAST-MODIFIED` and fixture signature.

```bash
# a match appears
python tools/simulate_change.py spitfire.ics --add-fake --at 22:15

# it is postponed, and moves venue -- same UID, so it must not duplicate
python tools/simulate_change.py spitfire.ics --uid simulated-test --at 22:45 \
  --venue "Slavie 3" --note "postponed"

# clean up (or let the next scheduled run do it)
python tools/simulate_change.py spitfire.ics --remove-fake
```

Commit and push between steps. Subscribed calendars are read-only in every client, so
the event's own fields have to carry the change you're watching for.

The synthetic event uses UID `simulated-test@futsalvplzni`. While its start time is
still ahead, the scheduled job removes it by itself and the shrink guard ignores it.
Left running past its own kickoff it counts as a played fixture and gets archived, so
clean it up with `--remove-fake`.

Real fixtures can be moved the same way; the next run restores the true time from the
site.

```bash
python tools/simulate_change.py spitfire.ics --index 0 --at "2026-09-08 20:00"
```
