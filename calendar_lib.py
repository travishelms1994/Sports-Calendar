"""Shared helpers for building subscribable .ics calendars.

Used by sports.py (and later music.py). Standard library + tzdata only.
"""

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

CENTRAL = ZoneInfo("America/Chicago")
USER_AGENT = "Mozilla/5.0 (personal calendar builder)"


# ---------------------------------------------------------------- fetching

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_text(url, retries=3, browser=False):
    last_error = None
    for attempt in range(retries):
        try:
            headers = BROWSER_HEADERS if browser else {"User-Agent": USER_AGENT}
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:  # network hiccup: wait and retry
            last_error = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Could not fetch {url}: {last_error}")


def fetch_json(url, retries=3):
    return json.loads(fetch_text(url, retries))


# ---------------------------------------------------------------- events

@dataclass
class Event:
    uid: str
    title: str
    # Timed events: start is a timezone-aware datetime.
    # All-day events: start is a date (and end, if given, is the last day, inclusive).
    start: object
    end: object = None
    location: str = ""
    description: str = ""
    url: str = ""
    duration_hours: float = 3.0
    categories: list = field(default_factory=list)

    @property
    def all_day(self):
        return not isinstance(self.start, datetime)

    def last_moment(self):
        """When the event is over (used to drop old events)."""
        if self.all_day:
            last_day = self.end or self.start
            return datetime.combine(last_day + timedelta(days=1), datetime.min.time(), CENTRAL)
        return self.end or self.start + timedelta(hours=self.duration_hours)


def parse_espn_date(s):
    """ESPN dates look like 2026-10-22T00:30Z."""
    s = s.replace("Z", "+00:00")
    if len(s) == len("2026-10-22T00:30+00:00"):
        s = s.replace("+00:00", ":00+00:00")
    return datetime.fromisoformat(s)


def central_date(dt):
    return dt.astimezone(CENTRAL).date()


# ---------------------------------------------------------------- reading .ics feeds

def parse_ics(text):
    """Minimal .ics reader: returns a list of dicts of VEVENT properties."""
    text = re.sub(r"\r?\n[ \t]", "", text)  # unfold continuation lines
    events, current = [], None
    for line in text.splitlines():
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            name, value = line.split(":", 1)
            key = name.split(";", 1)[0].upper()
            current[key] = value.replace("\\,", ",").replace("\\;", ";").replace("\\n", "\n").strip()
    return events


def parse_ics_datetime(value):
    """Returns a UTC datetime, or a date for all-day values."""
    if re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value, "%Y%m%d").date()
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=CENTRAL)


# ---------------------------------------------------------------- writing .ics

def _escape(text):
    return (text.replace("\\", "\\\\").replace(";", "\\;")
                .replace(",", "\\,").replace("\n", "\\n"))


def _fold(line):
    """Fold lines longer than 75 bytes, as the iCalendar spec requires."""
    out, current = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(current) + len(b) > 75:
            out.append(current.decode("utf-8"))
            current = b" " + b
        else:
            current += b
    out.append(current.decode("utf-8"))
    return "\r\n".join(out)


def _utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_ics(path, events, calendar_name, description=""):
    stamp = _utc(datetime.now(timezone.utc))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//travishelms1994//calendars//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(calendar_name)}",
        f"X-WR-CALDESC:{_escape(description)}",
        "X-WR-TIMEZONE:America/Chicago",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    for e in sorted(events, key=lambda e: (e.last_moment(), e.title)):
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{e.uid}")
        lines.append(f"DTSTAMP:{stamp}")
        if e.all_day:
            last_day = e.end or e.start
            lines.append(f"DTSTART;VALUE=DATE:{e.start.strftime('%Y%m%d')}")
            lines.append(f"DTEND;VALUE=DATE:{(last_day + timedelta(days=1)).strftime('%Y%m%d')}")
            lines.append("TRANSP:TRANSPARENT")  # banners don't block your time
        else:
            end = e.end or e.start + timedelta(hours=e.duration_hours)
            # Late games end at 11:59 PM Central so they don't spill onto the next day.
            local_start = e.start.astimezone(CENTRAL)
            day_end = local_start.replace(hour=23, minute=59, second=0, microsecond=0)
            end = min(end, day_end) if day_end > local_start else end
            lines.append(f"DTSTART:{_utc(e.start)}")
            lines.append(f"DTEND:{_utc(end)}")
        lines.append(f"SUMMARY:{_escape(e.title)}")
        if e.location:
            lines.append(f"LOCATION:{_escape(e.location)}")
        if e.description:
            lines.append(f"DESCRIPTION:{_escape(e.description)}")
        if e.url:
            lines.append(f"URL:{e.url}")
        if e.categories:
            lines.append(f"CATEGORIES:{','.join(_escape(c) for c in e.categories)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(_fold(l) for l in lines) + "\r\n")
