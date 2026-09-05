#!/usr/bin/env python3
"""Stage a fake change in a published .ics, to test how calendar clients react.

Three things worth testing, all of which this can produce:

  # 1. a new match appears
  python tools/simulate_change.py spitfire.ics --add-fake --at 22:15

  # 2. that match is postponed -- same UID, so it must move in place
  python tools/simulate_change.py spitfire.ics --uid simulated-test --at 22:45

  # 3. clean the fake up early, instead of waiting for the workflow
  python tools/simulate_change.py spitfire.ics --remove-fake

  # 4. a real fixture is postponed
  python tools/simulate_change.py spitfire.ics --index 0 --at "2026-09-08 20:00"

Every edit mirrors exactly what run.py would write: the UID is left alone, and
SEQUENCE, LAST-MODIFIED, DTSTAMP and X-FIXTURE-SIGNATURE are updated. A client
that honours the standard shows the existing event moving, keeping any alert or
note attached to it, rather than adding a second one.

Nothing needs undoing by hand. The next workflow run compares the file against
the real schedule: a moved fixture is written back to its true time, and the
fake event -- which is on no schedule and still in the future -- is dropped as
a cancellation.
"""
import argparse
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

STAMP = "%Y%m%dT%H%M%SZ"
TZ = ZoneInfo("Europe/Prague")
MATCH_MINUTES = 60
FAKE_UID = "simulated-test@futsalvplzni"


def parse_when(text: str) -> datetime:
    """Accept 'HH:MM' (today, Prague time) or 'YYYY-MM-DD HH:MM'."""
    for fmt, today_only in (("%H:%M", True), ("%Y-%m-%d %H:%M", False), ("%d.%m.%Y %H:%M", False)):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if today_only:
            now = datetime.now(TZ)
            parsed = parsed.replace(year=now.year, month=now.month, day=now.day)
        return parsed.replace(tzinfo=TZ).astimezone(timezone.utc)
    raise argparse.ArgumentTypeError(
        f"Cannot read {text!r}; use 'HH:MM', 'YYYY-MM-DD HH:MM' or 'DD.MM.YYYY HH:MM'")


def split_events(text: str) -> list[str]:
    """Split a calendar into chunks, every second one being a VEVENT block."""
    return re.split(r"(BEGIN:VEVENT\r?\n.*?END:VEVENT\r?\n)", text, flags=re.S)


def field(block: str, name: str) -> str:
    match = re.search(rf"^{name}:(.*?)\r?$", block, flags=re.M)
    return match.group(1) if match else ""


def put(block: str, name: str, value: str) -> str:
    """Replace a property, keeping the CRLF that iCalendar requires."""
    return re.sub(rf"^{name}:.*?\r?$", f"{name}:{value}\r", block, flags=re.M)


def move(block: str, start: datetime, summary: str | None = None,
         venue: str | None = None, note: str | None = None) -> str:
    now = datetime.now(timezone.utc).strftime(STAMP)
    end = start + timedelta(minutes=MATCH_MINUTES)

    if summary is not None:
        block = put(block, "SUMMARY", summary)
    if venue is not None:
        block = put(block, "LOCATION", venue)
    if note is not None:
        block = put(block, "DESCRIPTION", note)

    signature = field(block, "X-FIXTURE-SIGNATURE")
    if signature:
        # The signature is start|venue|... -- keep whatever follows the venue,
        # so a real fixture's team names survive a venue-only edit.
        segments = signature.split("|")
        segments[0] = start.strftime(STAMP)
        if venue is not None and len(segments) > 1:
            segments[1] = venue
        block = put(block, "X-FIXTURE-SIGNATURE", "|".join(segments))

    sequence = field(block, "SEQUENCE")
    block = put(block, "SEQUENCE", str(int(sequence) + 1 if sequence.isdigit() else 1))
    block = put(block, "DTSTART", start.strftime(STAMP))
    block = put(block, "DTEND", end.strftime(STAMP))
    block = put(block, "LAST-MODIFIED", now)
    return put(block, "DTSTAMP", now)


def fake_event(start: datetime, summary: str, venue: str, note: str | None = None) -> str:
    now = datetime.now(timezone.utc).strftime(STAMP)
    end = start + timedelta(minutes=MATCH_MINUTES)
    signature = f"{start.strftime(STAMP)}|{venue}|{summary}"
    lines = [
        "BEGIN:VEVENT",
        f"UID:{FAKE_UID}",
        f"DTSTAMP:{now}",
        "SEQUENCE:0",
        f"LAST-MODIFIED:{now}",
        f"DTSTART:{start.strftime(STAMP)}",
        f"DTEND:{end.strftime(STAMP)}",
        f"SUMMARY:{summary}",
        f"DESCRIPTION:{note or 'Testovaci udalost - pri dalsim behu workflow zmizi'}",
        f"LOCATION:{venue}",
        "URL:https://futsalvplzni.cz/rozpis",
        f"X-FIXTURE-SIGNATURE:{signature}",
        "END:VEVENT",
        "",
    ]
    return "\r\n".join(lines)


def describe(block: str, prefix: str) -> None:
    start = field(block, "DTSTART")
    local = datetime.strptime(start, STAMP).replace(tzinfo=timezone.utc).astimezone(TZ)
    print(f"{prefix} {field(block, 'SUMMARY')}")
    print(f"  UID       {field(block, 'UID')}")
    print(f"  DTSTART   {start}  ({local:%d.%m.%Y %H:%M} Praha)")
    print(f"  LOCATION  {field(block, 'LOCATION')}")
    print(f"  NOTE      {field(block, 'DESCRIPTION')}")
    print(f"  SEQUENCE  {field(block, 'SEQUENCE')}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", help="Published .ics file to edit in place")
    parser.add_argument("--at", type=parse_when, metavar="TIME",
                        help="Move to this time: 'HH:MM' (today), 'YYYY-MM-DD HH:MM' "
                             "or 'DD.MM.YYYY HH:MM'")
    parser.add_argument("--hours", type=float, metavar="N",
                        help="Move by N hours instead of to a fixed time")
    parser.add_argument("--add-fake", action="store_true",
                        help=f"Append a synthetic event with UID {FAKE_UID}")
    parser.add_argument("--remove-fake", action="store_true",
                        help="Delete that synthetic event again")
    parser.add_argument("--summary", help="Set the title (default for --add-fake: "
                                          "'Spitfire vs TEST')")
    parser.add_argument("--venue", help="Set the location (default for --add-fake: "
                                        "'Krasovska')")
    parser.add_argument("--note", help="Set the description")
    parser.add_argument("--uid", help="Pick the event to move by UID (substring match)")
    parser.add_argument("--index", type=int, help="Pick the event to move by position, 0-based")
    args = parser.parse_args()

    with open(args.path, encoding="utf-8", newline="") as f:
        parts = split_events(f.read())
    positions = [i for i, part in enumerate(parts) if part.startswith("BEGIN:VEVENT")]

    if args.remove_fake:
        # The workflow drops the fake event by itself while its start time is
        # still ahead, treating it as a cancellation. Once that time has passed
        # it counts as a played fixture and is archived instead, so a test left
        # running past its own kickoff has to be cleaned up here.
        doomed = [i for i in positions if FAKE_UID in field(parts[i], "UID")]
        if not doomed:
            print(f"No event with UID {FAKE_UID} in {args.path}")
            return 0
        for i in reversed(doomed):
            describe(parts[i], "Removed:")
            parts.pop(i)
    elif args.add_fake:
        if args.at is None:
            parser.error("--add-fake needs --at")
        if any(FAKE_UID in parts[i] for i in positions):
            parser.error(f"{args.path} already holds an event with UID {FAKE_UID}")
        block = fake_event(args.at, args.summary or "Spitfire vs TEST",
                           args.venue or "Krasovska", args.note)
        # Order does not matter to clients, but keeping the file sorted by start
        # time makes hand-inspecting it far easier.
        after = [i for i in positions if field(parts[i], "DTSTART") > args.at.strftime(STAMP)]
        parts.insert(after[0] if after else (positions[-1] + 1 if positions else len(parts) - 1),
                     block)
        describe(block, "Added:")
    else:
        if args.at is None and args.hours is None:
            parser.error("give --at or --hours (or --add-fake / --remove-fake)")
        if not positions:
            print(f"No events in {args.path}", file=sys.stderr)
            return 1

        if args.uid:
            chosen = [i for i in positions if args.uid in field(parts[i], "UID")]
            if len(chosen) != 1:
                print(f"--uid {args.uid!r} matched {len(chosen)} events; be more specific",
                      file=sys.stderr)
                return 1
            target = chosen[0]
        else:
            index = args.index or 0
            if not 0 <= index < len(positions):
                print(f"--index {index} out of range ({len(positions)} events)", file=sys.stderr)
                return 1
            target = positions[index]

        if args.at is not None:
            start = args.at
        else:
            current = datetime.strptime(field(parts[target], "DTSTART"), STAMP)
            start = current.replace(tzinfo=timezone.utc) + timedelta(hours=args.hours)
        describe(parts[target], "Was:")
        parts[target] = move(parts[target], start, args.summary, args.venue, args.note)
        describe(parts[target], "Now:")

    with open(args.path, "w", encoding="utf-8", newline="") as f:
        f.write("".join(parts))

    print("\nCommit and push, then wait for the client to refresh.")
    print("GitHub Pages caches for 10 minutes, so allow at least that.")
    print("The next workflow run tidies up on its own.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
