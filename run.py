#!/usr/bin/env python3
import os
import re
import sys
import uuid
import argparse
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup

BASE = "https://futsalvplzni.cz"
URL  = f"{BASE}/rozpis"
TZ   = ZoneInfo("Europe/Prague")
UA   = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124 Safari/537.36"}
MATCH_MINUTES = 60

def norm(s: str) -> str:
    """casefold + strip diacritics (for robust matching)"""
    if s is None:
        return ""
    return "".join(c for c in unicodedata.normalize("NFKD", s.casefold())
                   if not unicodedata.combining(c))

def fetch_html(url: str, params: dict | None = None) -> str:
    r = requests.get(url, params=params, timeout=30, headers=UA)
    r.raise_for_status()
    return r.text

def resolve_team_id_from_unfiltered(team_name: str) -> int:
    """Open /rozpis (unfiltered), find first <a href='/tym/<ID>/...'> whose text matches team_name (substring, case/diacritics-insensitive)."""
    html = fetch_html(URL)
    soup = BeautifulSoup(html, "html.parser")
    wanted = norm(team_name)
    for a in soup.select("table.table.table-striped.mt-2.small a[href^='/tym/']"):
        name = a.get_text(" ", strip=True)
        if wanted in norm(name):  # keep original logic (substring)
            m = re.search(r"/tym/(\d+)/", a.get("href",""))
            if m:
                return int(m.group(1))
    raise ValueError(f"Team not found on unfiltered schedule: {team_name!r}")

def parse_rows(html: str):
    """Parse schedule rows into dicts."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for table in soup.select("table.table.table-striped.mt-2.small"):
        th_texts = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        idx = {"date": 0, "time": 2, "comp": 3, "venue": 4, "home": 5, "away": 6}
        name_map = {"datum": "date", "čas": "time", "soutěž": "comp", "hřiště": "venue"}
        for i, th in enumerate(th_texts):
            if th in name_map:
                idx[name_map[th]] = i
        for tbody in table.find_all("tbody"):
            for tr in tbody.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) < 7:
                    continue
                def cell(i): return tds[i].get_text(" ", strip=True).replace("\xa0"," ")
                date_text, time_text = cell(idx["date"]), cell(idx["time"])
                comp, venue = cell(idx["comp"]), cell(idx["venue"])
                home, away  = cell(idx["home"]), cell(idx["away"])
                try:
                    dt = datetime.strptime(f"{date_text} {time_text}", "%d.%m.%Y %H:%M").replace(tzinfo=TZ)
                except ValueError:
                    continue
                rows.append({"date": dt, "competition": comp, "venue": venue, "home": home, "away": away})
    def key(r):
        return (r["date"], r["competition"], r["venue"], r["home"], r["away"])
    return list({key(r): r for r in rows}.values())

def esc(s):  # iCalendar escaping
    return s.replace("\\","\\\\").replace(",","\\,").replace(";","\\;").replace("\n","\\n")

def fixture_uid(team_id: int, r) -> str:
    """Stable per-fixture UID.

    The kickoff time is deliberately NOT part of the identity: a postponed match
    keeps the same UID, so subscribers get an update to the existing event
    instead of a delete plus an unrelated new one (which would drop their
    reminders and notes).
    """
    ident = "|".join([
        str(team_id),
        norm(r["competition"]),
        norm(r["home"]),
        norm(r["away"]),
    ])
    return f"{uuid.uuid5(uuid.NAMESPACE_URL, ident)}@futsalvplzni"

def unfold_ics(text: str) -> list[str]:
    """Undo RFC 5545 line folding and return logical lines."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines

def read_previous(path: str) -> dict[str, dict]:
    """Read the previously published .ics so SEQUENCE can be carried forward.

    The committed calendar file is the only state store; there is no database.
    Returns {uid: {"sequence": int, "signature": str}}.
    """
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return {}

    events: dict[str, dict] = {}
    current: dict | None = None
    fields = {"DTSTAMP": "dtstamp", "LAST-MODIFIED": "last_modified",
              "X-FIXTURE-SIGNATURE": "signature"}
    for line in unfold_ics(text):
        if line == "BEGIN:VEVENT":
            current = {"sequence": 0, "signature": "", "dtstamp": "",
                       "last_modified": "", "start": None, "lines": [line]}
        elif current is None:
            continue
        elif line == "END:VEVENT":
            current["lines"].append(line)
            uid = current.pop("uid", None)
            if uid:
                events[uid] = current
            current = None
        else:
            current["lines"].append(line)
            name, _, value = line.partition(":")
            name = name.split(";", 1)[0].upper()
            if name == "UID":
                current["uid"] = value
            elif name == "SEQUENCE":
                try:
                    current["sequence"] = int(value)
                except ValueError:
                    pass
            elif name == "DTSTART":
                try:
                    current["start"] = datetime.strptime(
                        value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
            elif name in fields:
                current[fields[name]] = value
    return events

def signature(r) -> str:
    """Everything a subscriber would want to be re-notified about."""
    return "|".join([
        r["date"].astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        r["venue"],
        r["home"],
        r["away"],
    ])

def to_ics(rows, calname, team_id: int, schedule_url: str | None = None,
           previous: dict[str, dict] | None = None):
    previous = previous or {}
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    out = [
        "BEGIN:VCALENDAR","VERSION:2.0","CALSCALE:GREGORIAN",
        "PRODID:-//FutsalPlzen//Schedule//CZ", f"X-WR-CALNAME:{esc(calname)}",
        "X-WR-TIMEZONE:Europe/Prague",
    ]
    blocks: list[tuple[datetime, list[str]]] = []
    seen: set[str] = set()
    for r in sorted(rows, key=lambda x: x["date"]):
        # Emit UTC instants rather than TZID references: no VTIMEZONE component
        # is then required, and clients without a tz database cannot shift them.
        start = r["date"].astimezone(timezone.utc)
        end = start + timedelta(minutes=MATCH_MINUTES)
        uid = fixture_uid(team_id, r)
        sig = signature(r)
        prev = previous.get(uid)
        # Timestamps are only refreshed when something a subscriber cares about
        # actually changed, so an unchanged schedule re-serialises byte for byte
        # and the weekly job has nothing to commit.
        if prev is None:
            seq, dtstamp, modified = 0, stamp, stamp
        elif prev["signature"] == sig:
            seq = prev["sequence"]
            dtstamp = prev["dtstamp"] or stamp
            modified = prev["last_modified"] or stamp
        else:
            seq, dtstamp, modified = prev["sequence"] + 1, stamp, stamp
        seen.add(uid)
        blocks.append((start, [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{dtstamp}",
            f"SEQUENCE:{seq}",
            f"LAST-MODIFIED:{modified}",
            f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}",
            f"SUMMARY:{esc(r['home']+' vs '+r['away'])}",
            f"DESCRIPTION:{esc('Soutěž: ' + r['competition'])}",
            f"LOCATION:{esc(r['venue'])}",
            f"URL:{schedule_url if schedule_url else URL}",
            f"X-FIXTURE-SIGNATURE:{esc(sig)}",
            "END:VEVENT"
        ]))

    # The site lists upcoming fixtures only, so a played match simply drops off
    # it. Carry such events over from the last published file, otherwise every
    # subscriber would lose the season's history from their calendar a week at
    # a time. Fixtures still in the future that vanished were cancelled, and are
    # allowed to disappear.
    for uid, prev in previous.items():
        if uid in seen or prev["start"] is None or prev["start"] >= now:
            continue
        blocks.append((prev["start"], prev["lines"]))

    for _, lines in sorted(blocks, key=lambda b: b[0]):
        out += lines
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"

def run(team: str, outfile: str, want_ics: bool) -> int:
    # Resolve ID
    try:
        team_id = resolve_team_id_from_unfiltered(team)
    except ValueError:
        print(f"Team not found: {team}")
        return 1
    except requests.RequestException as e:
        print(f"Network error while loading schedule: {e}")
        return 1

    # Fetch filtered
    try:
        html = fetch_html(URL, params={"teamList": str(team_id)})
    except requests.RequestException as e:
        print(f"Network error while fetching filtered schedule for team ID {team_id}: {e}")
        return 1

    # Parse & output
    rows = parse_rows(html)
    if not rows:
        print(f"No matches found after filtering for team '{team}' (ID {team_id}).")
        return 1

    for r in sorted(rows, key=lambda x: x["date"]):
        print(
            r["date"].strftime("%Y-%m-%d %H:%M"),
            f"[{r['competition']}]",
            f"{r['venue']}: {r['home']} vs {r['away']}"
        )

    if want_ics:
        try:
            filtered_url = f"{URL}?teamList={team_id}"
            previous = read_previous(outfile)
            ics_text = to_ics(rows, f"Futsal Plzeň — {team}", team_id,
                              schedule_url=filtered_url, previous=previous)
            with open(outfile, "w", encoding="utf-8", newline="") as f:
                f.write(ics_text)
            print(f"\nSaved ICS: {outfile} — {len(rows)} events")
        except OSError as e:
            print(f"Could not write ICS file '{outfile}': {e}")
            return 1
    else:
        print(f"\nTotal matches: {len(rows)} (team: {team} / ID {team_id})")

    return 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export the Futsal Plzeň schedule for a team")
    parser.add_argument("--team", default=os.environ.get("FUTSAL_TEAM"),
                        help="Team name (substring; case/diacritics-insensitive). Defaults to $FUTSAL_TEAM.")
    parser.add_argument("--out", default="schedule.ics", help="Output .ics path (if --ics)")
    parser.add_argument("--ics", action="store_true", help="Also save an .ics calendar")
    args = parser.parse_args()
    if not args.team:
        parser.error("--team is required (or set FUTSAL_TEAM)")
    sys.exit(run(args.team, args.out, args.ics))
