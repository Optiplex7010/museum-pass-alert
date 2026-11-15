#!/usr/bin/env python3
"""Email me when a free Seattle Public Library / KCLS museum pass opens up.

Both libraries run Springshare LibCal. Each museum page loads a month grid from
/pass/availability/institution; a day is bookable when its cell has the class
"s-lc-pass-available". We read that grid, compare with the last run (state.json)
and email only days that are newly available.

Standard library only. Config comes from environment variables:
  GMAIL_USER          sender Gmail address (hrshimada28@gmail.com)
  GMAIL_APP_PASSWORD  Gmail app password (NOT the normal password)
  MAIL_TO             optional, defaults to GMAIL_USER
Flags: --dry-run     print emails instead of sending, and do not save state
       --test-email  send one test email and exit
"""
import html
import json
import os
import re
import smtplib
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")
STATE_FILE = Path(__file__).with_name("state.json")
MAX_EMAILS_PER_RUN = 10  # stops a flood on the first run; the rest go out next run

SPL = {"name": "Seattle Public Library", "base": "https://spl.libcal.com"}
KCLS = {"name": "King County Library", "base": "https://rooms.kcls.org"}

# (museum name used in the email, library, page slug used in the booking URL, LibCal museum id)
# IDs were read from each library's pass page. Woodland Zoo = Woodland Park Zoo.
WATCH = [
    ("MOHAI", SPL, "MOHAI", "459fd25dbae0"),
    ("MOHAI", KCLS, "dcb899890d0c", "dcb899890d0c"),
    ("Museum of Flight", SPL, "Flight", "3cec43a0d6e8"),
    ("Museum of Flight", KCLS, "ba4a1c71f547", "ba4a1c71f547"),
    ("Northwest African American Museum", SPL, "NAANNW", "52e2c9c73cca"),
    ("Northwest African American Museum", KCLS, "b03f547b9c80", "b03f547b9c80"),
    ("Burke Museum", SPL, "Burke", "ade05ae45e52"),
    ("Henry Art Gallery", SPL, "Henry", "d22bdf1469f1"),
    ("Volunteer Park Conservatory", SPL, "VolunteerParkConservatory", "cdfe5e189647"),
    ("Seattle Japanese Garden", SPL, "JapaneseGarden", "851b3af93bbc"),
    ("Pacific Science Center", SPL, "PacSci", "c4018b02a280"),
    ("Woodland Park Zoo", SPL, "Zoo", "033bbf08993f"),
    ("Woodland Park Zoo", KCLS, "15d03dcb51d3", "15d03dcb51d3"),
]

CELL = re.compile(
    r'class="day [^"]*?day-(\d{4}-\d\d-\d\d)[^"]*"\s*>\s*<div class="day-number">\s*'
    r'<span class="s-lc-pass-availability (s-lc-pass-[a-z-]+)'
)


def month_starts(today):
    """First day of this month and next (passes can be booked ~30 days ahead)."""
    first = today.replace(day=1)
    nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    return [first, nxt]


def fetch_month(library, museum_id, month_start):
    qs = urllib.parse.urlencode({
        "museum": museum_id, "date": month_start.isoformat(),
        "digital": "true", "physical": "false", "location": 0,
    })
    req = urllib.request.Request(
        f"{library['base']}/pass/availability/institution?{qs}",
        headers={"User-Agent": "Mozilla/5.0 (personal museum-pass notifier)",
                 "X-Requested-With": "XMLHttpRequest"},
    )
    body = None
    for attempt in range(4):  # the libraries' servers sometimes drop a connection; retry
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read().decode("utf-8", "replace")
            break
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
    cells = CELL.findall(body)
    if not cells:  # layout changed or error page: fail loudly rather than go silent
        raise RuntimeError(f"no calendar cells found ({body[:80]!r})")
    return cells


def available_days(item, today):
    museum, library, _slug, museum_id = item
    found = set()
    for month in month_starts(today):
        for day, status in fetch_month(library, museum_id, month):
            if status == "s-lc-pass-available" and date.fromisoformat(day) >= today:
                found.add(day)
    return found


def subject_for(museum, library, day, today):
    if day == today:
        return f"TODAY {museum} — free {library['name']} museum pass"
    return f"{day.strftime('%A')}, {day.strftime('%b')} {day.day} {museum} — free {library['name']} museum pass"


def send(msg_subject, body, dry_run):
    if dry_run:
        print(f"[dry-run] {msg_subject}\n{body}\n")
        return
    user = os.environ["GMAIL_USER"]
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = msg_subject, user, os.environ.get("MAIL_TO", user)
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(user, os.environ["GMAIL_APP_PASSWORD"])
        s.send_message(msg)


def main():
    dry_run = "--dry-run" in sys.argv
    if "--test-email" in sys.argv:  # one-off check that Gmail login and delivery work
        send("TEST museum pass alert — email setup works",
             "If you can read this, the museum pass alerts can reach you.\n", dry_run)
        print("test email sent" if not dry_run else "test email (dry run)")
        return
    today = datetime.now(TZ).date()
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    errors = []

    def work(item):
        try:
            return item, available_days(item, today), None
        except Exception as e:  # keep scanning the other museums
            return item, None, e

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(work, WATCH))

    sent = 0
    for item, days, err in results:
        museum, library, slug, _ = item
        key = f"{library['base']}|{museum}"
        if err is not None:
            errors.append(f"{museum} @ {library['name']}: {err}")
            continue  # keep old state for this museum
        already = set(state.get(key, []))
        notified = already & days  # still available, already told you
        for day in sorted(days - already):
            if sent >= MAX_EMAILS_PER_RUN:
                break  # not marked as notified, so it goes out next run
            d = date.fromisoformat(day)
            url = f"{library['base']}/passes/{slug}"
            body = (f"{museum}: a free {library['name']} pass is available for "
                    f"{d.strftime('%A, %B')} {d.day}.\n\nBook it: {url}\n\n"
                    "Passes go fast. Reservations are limited per library card.\n")
            try:
                send(subject_for(museum, library, d, today), body, dry_run)
            except Exception as e:
                errors.append(f"email failed for {museum} {day}: {e}")
                continue
            notified.add(day)
            sent += 1
        state[key] = sorted(notified)  # days that vanish are dropped, so a re-release emails again

    if not dry_run:
        STATE_FILE.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")
    print(f"{datetime.now(TZ):%Y-%m-%d %H:%M} checked {len(WATCH)} passes, sent {sent} email(s)")
    for e in errors:
        print("ERROR:", html.unescape(e), file=sys.stderr)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
