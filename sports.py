"""Builds site/sports.ics: a self-updating sports calendar.

Runs every day on GitHub Actions. Every run rebuilds the whole file from the
sources below, so times that get set, new games, playoff matchups, this week's
Top 25 games and tennis draws all show up on their own.

THE RULES live in the section right below. Change them there.
"""

import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

from calendar_lib import (CENTRAL, Event, central_date, fetch_json, fetch_text,
                          parse_espn_date, parse_ics, parse_ics_datetime, write_ics)

# =============================================================== THE RULES

CALENDAR_NAME = "Sports"

# TCU teams, read from TCU's own calendar feeds (gofrogs.com sport ids).
TCU_SPORTS = {
    "TCU Football": 3,
    "TCU Basketball": 5,   # men's
    "TCU Baseball": 1,
}
SKIP_EXHIBITIONS = True

# Pro teams: every regular-season and playoff game. (ESPN league path, team id, hours)
PRO_TEAMS = {
    "Rockets": ("basketball/nba", 10, 2.5),
    "Texans": ("football/nfl", 34, 3.5),
    "Astros": ("baseball/mlb", 18, 3.0),
}

# NFL: games not played on Sunday (Thursday, Monday, Saturday, holidays),
# an all-day "NFL Week N" banner on each Sunday, and all playoff games.
NFL_NON_SUNDAY_GAMES = True
NFL_WEEK_BANNERS = True
NFL_PLAYOFFS = True

# College football: games where BOTH teams are in the AP Top 25, for the next 7 days.
CFB_TOP25_MATCHUPS = True
CFB_TOP25_DAYS_AHEAD = 7
COLLEGE_FOOTBALL_PLAYOFF = True

# MLB postseason series to include.
MLB_SERIES = ("ALCS", "NLCS", "World Series")

# Golf: all-day banners for these events (read from ESPN's golf calendar).
GOLF_EVENTS = {
    "Masters Tournament": "Masters",
    "PGA Championship": "PGA Championship",
    "U.S. Open": "U.S. Open (Golf)",
    "The Open": "The Open Championship",
    "The Open Championship": "The Open Championship",
    "Ryder Cup": "Ryder Cup",
}

# Tennis: all-day banners for each Grand Slam, plus men's singles matches
# from these rounds on. ESPN doesn't publish future Slam dates, so ADD NEXT
# YEAR'S DATES HERE once they're announced. (name, first day, last day)
TENNIS_ROUNDS = ("Round 4", "Quarterfinal", "Semifinal", "Final")
TENNIS_DRAW = "Men's Singles"
TENNIS_SLAMS = [
    ("Australian Open", date(2027, 1, 17), date(2027, 1, 31)),
    ("French Open", date(2027, 5, 23), date(2027, 6, 6)),
    ("Wimbledon", date(2027, 6, 28), date(2027, 7, 11)),
    ("US Open (Tennis)", date(2027, 8, 29), date(2027, 9, 12)),
]

# Other fixed all-day banners (Olympics etc.). (name, first day, last day, location)
OTHER_BANNERS = [
    ("Summer Olympics", date(2028, 7, 14), date(2028, 7, 30), "Los Angeles"),
]

# Drop events that ended more than this many days ago.
KEEP_PAST_DAYS = 14

# =============================================================== sources

ESPN = "https://site.api.espn.com/apis/site/v2/sports"
TCU_ESPN_ID = "2628"  # TCU in ESPN college football; its games come from TCU's own feed
NOW = datetime.now(timezone.utc)
TODAY = central_date(NOW)


def tcu_games():
    events = []
    for label, sport_id in TCU_SPORTS.items():
        url = f"https://gofrogs.com/calendar.ashx/calendar.ics?sport_id={sport_id}"
        for raw in parse_ics(fetch_text(url)):
            summary = raw.get("SUMMARY", "")
            if SKIP_EXHIBITIONS and re.search(r"\(Exh\.?\)|Exhibition|Scrimmage", summary, re.I):
                continue
            # "[W] TCU Men's Basketball vs Lindenwood" -> "TCU Basketball vs Lindenwood"
            summary = re.sub(r"^\[[WLT]\]\s*", "", summary)
            opponent = re.sub(r"^TCU [^v@]*?(?=\b(vs|at)\b)", "", summary).strip()
            # "vs Kansas - Haunting at The Carter": promo name goes in the notes
            opponent, _, promo = opponent.partition(" - ")
            title = f"{label} {opponent}"
            start = parse_ics_datetime(raw["DTSTART"])
            if not isinstance(start, datetime):
                title += " (time TBA)"
            # Lines after the first are the result, TV and radio
            notes = [promo] + raw.get("DESCRIPTION", "").split("\n")[1:]
            events.append(Event(
                uid=f"tcu-{raw.get('UID', title + str(start))}",
                title=title,
                start=start,
                location=raw.get("LOCATION", "").strip(),
                description="\n".join(n.strip() for n in notes if n.strip()),
                url=raw.get("URL", "").replace("&amp;", "&"),
                duration_hours=3.5 if "Football" in label else 3.0 if "Baseball" in label else 2.5,
                categories=["TCU"],
            ))
    return events


def _team_name(competitor):
    team = competitor.get("team") or {}
    name = team.get("shortDisplayName") or team.get("displayName") or "TBD"
    return "TBD" if "TBD" in name else name


def _score(competitor):
    s = competitor.get("score")
    if isinstance(s, dict):
        return s.get("displayValue", "")
    return s or ""


def _matchup(comp, rank=False, use_location=False):
    teams = {c.get("homeAway"): c for c in comp.get("competitors", [])}
    away, home = teams.get("away", {}), teams.get("home", {})

    def name(c):
        n = (c.get("team") or {}).get("location") if use_location else None
        n = n or _team_name(c)
        r = (c.get("curatedRank") or {}).get("current", 99)
        return f"#{r} {n}" if rank and r <= 25 else n

    sep = "vs" if comp.get("neutralSite") else "@"
    return f"{name(away)} {sep} {name(home)}", away, home


def _teams_unknown(matchup):
    return matchup in ("TBD @ TBD", "TBD vs TBD")


def game_day(ev):
    """The Central-time day of an ESPN game. Games without a set time are stored
    at midnight Eastern / 11 PM Central the night before, so shift those forward."""
    start = parse_espn_date(ev["date"])
    if ev["competitions"][0].get("timeValid", True) is False:
        start += timedelta(hours=6)
    return central_date(start)


def espn_event(ev, league, title=None, hours=3.0, category="", rank=False, use_location=False):
    """Turns an ESPN event (from a schedule or scoreboard) into a calendar Event."""
    comp = ev["competitions"][0]
    matchup, away, home = _matchup(comp, rank, use_location)
    title = title or matchup
    status = comp.get("status") or ev.get("status") or {}
    state = (status.get("type") or {}).get("name", "")
    if state in ("STATUS_POSTPONED", "STATUS_CANCELED"):
        title += " (Postponed)" if state == "STATUS_POSTPONED" else " (Canceled)"

    start = parse_espn_date(ev["date"])
    if comp.get("timeValid", True) is False:
        start = game_day(ev)
        title += " (time TBA)"

    details = []
    if (status.get("type") or {}).get("completed"):
        details.append(f"Final: {_team_name(away)} {_score(away)}, {_team_name(home)} {_score(home)}")
    tv = []
    for b in comp.get("broadcasts") or []:
        tv += b.get("names") or [((b.get("media") or {}).get("shortName"))]
    tv = [t for t in dict.fromkeys(tv) if t]
    if tv:
        details.append("TV: " + ", ".join(tv))

    venue = comp.get("venue") or {}
    addr = venue.get("address") or {}
    where = ", ".join(x for x in (venue.get("fullName"), addr.get("city"), addr.get("state")) if x)

    return Event(
        uid=f"espn-{league.replace('/', '-')}-{ev['id']}",
        title=title,
        start=start,
        location=where,
        description="\n".join(details),
        duration_hours=hours,
        categories=[category] if category else [],
    )


def _note(comp):
    return " ".join(n.get("headline", "") for n in comp.get("notes") or []).strip()


def pro_team_games():
    events = []
    for name, (league, team_id, hours) in PRO_TEAMS.items():
        # Ask for this season and next, so the calendar rolls into new seasons on its own.
        for season in (TODAY.year, TODAY.year + 1):
            for season_type in (2, 3):  # regular season, playoffs
                url = f"{ESPN}/{league}/teams/{team_id}/schedule?season={season}&seasontype={season_type}"
                try:
                    data = fetch_json(url)
                except RuntimeError:
                    if season == TODAY.year + 1:
                        continue  # next season not published yet
                    raise
                for ev in data.get("events", []):
                    note = _note(ev["competitions"][0])
                    title = None
                    if note and (season_type == 3 or "Cup" in note):
                        title = f"{espn_matchup_title(ev)} ({note})"
                    events.append(espn_event(ev, league, title, hours, name))
    return events


def espn_matchup_title(ev):
    return _matchup(ev["competitions"][0])[0]


def nfl_season():
    return TODAY.year if TODAY.month >= 3 else TODAY.year - 1


def nfl_games():
    events = []
    season = nfl_season()
    if NFL_NON_SUNDAY_GAMES or NFL_WEEK_BANNERS:
        for week in range(1, 19):
            data = fetch_json(f"{ESPN}/football/nfl/scoreboard?seasontype=2&week={week}&dates={season}")
            sundays = set()
            for ev in data.get("events", []):
                day = game_day(ev)
                if day.weekday() == 6:
                    sundays.add(day)
                elif NFL_NON_SUNDAY_GAMES:
                    events.append(espn_event(ev, "football/nfl", hours=3.5, category="NFL"))
            if NFL_WEEK_BANNERS and sundays:
                sunday = min(sundays)
                events.append(Event(uid=f"nfl-week-{season}-{week}", title=f"NFL Week {week}",
                                    start=sunday, categories=["NFL"]))
    if NFL_PLAYOFFS:
        for week in range(1, 6):
            data = fetch_json(f"{ESPN}/football/nfl/scoreboard?seasontype=3&week={week}&dates={season}")
            for ev in data.get("events", []):
                note = _note(ev["competitions"][0]) or "NFL Playoffs"
                if "Pro Bowl" in note:
                    continue
                matchup = espn_matchup_title(ev)
                title = note if _teams_unknown(matchup) else f"{note}: {matchup}"
                events.append(espn_event(ev, "football/nfl", title, 3.5, "NFL"))
    return events


def _involves_tcu(ev):
    return any((c.get("team") or {}).get("id") == TCU_ESPN_ID
               for c in ev["competitions"][0].get("competitors", []))


def college_football():
    events = []
    league = "football/college-football"
    if CFB_TOP25_MATCHUPS:
        current = fetch_json(f"{ESPN}/{league}/scoreboard?groups=80&limit=300")
        week = (current.get("week") or {}).get("number")
        season_type = (current.get("season") or {}).get("type", 2)
        season = (current.get("season") or {}).get("year", nfl_season())
        if week and season_type == 2:
            for w in (week, week + 1):
                data = fetch_json(f"{ESPN}/{league}/scoreboard?groups=80&limit=300"
                                  f"&seasontype=2&week={w}&dates={season}")
                for ev in data.get("events", []):
                    comp = ev["competitions"][0]
                    ranks = [(c.get("curatedRank") or {}).get("current", 99) for c in comp["competitors"]]
                    day = game_day(ev)
                    if (all(r <= 25 for r in ranks) and not _involves_tcu(ev)
                            and TODAY <= day <= TODAY + timedelta(days=CFB_TOP25_DAYS_AHEAD)):
                        events.append(espn_event(ev, league, hours=3.5, category="Top 25",
                                                 rank=True, use_location=True))
    if COLLEGE_FOOTBALL_PLAYOFF:
        data = fetch_json(f"{ESPN}/{league}/scoreboard?seasontype=3&week=1&dates={nfl_season()}&limit=100")
        for ev in data.get("events", []):
            note = _note(ev["competitions"][0])
            if "College Football Playoff" not in note:
                continue
            note = (note.replace("College Football Playoff", "CFP")
                        .replace(" at the ", " – "))
            note = re.sub(r"\s+Presented by.*$", "", note, flags=re.I)
            matchup = _matchup(ev["competitions"][0], rank=False, use_location=True)[0]
            title = note if "TBD" in matchup else f"{note}: {matchup}"
            events.append(espn_event(ev, league, title, 3.5, "CFP"))
    return events


def mlb_postseason():
    events = []
    year = TODAY.year
    window_start, window_end = date(year, 9, 25), date(year, 11, 10)
    if not (window_start - timedelta(days=30) <= TODAY <= window_end):
        return events
    day = max(window_start, TODAY - timedelta(days=KEEP_PAST_DAYS))
    while day <= window_end:
        data = fetch_json(f"{ESPN}/baseball/mlb/scoreboard?dates={day:%Y%m%d}")
        for ev in data.get("events", []):
            note = _note(ev["competitions"][0])
            if not note.startswith(MLB_SERIES):
                continue
            # "World Series - Game 6 If Necessary" -> "World Series Game 6 (if necessary)"
            label = note.replace(" - ", " ").replace(" If Necessary", " (if necessary)")
            matchup = espn_matchup_title(ev)
            title = label if _teams_unknown(matchup) else f"{label}: {matchup}"
            events.append(espn_event(ev, "baseball/mlb", title, 3.0, "MLB Playoffs"))
        day += timedelta(days=1)
    return events


def golf_banners():
    events = []
    for year in (TODAY.year, TODAY.year + 1):
        data = fetch_json(f"{ESPN}/golf/pga/scoreboard?dates={year}")
        for item in (data.get("leagues") or [{}])[0].get("calendar", []):
            label = item.get("label", "")
            if label not in GOLF_EVENTS:
                continue
            start = central_date(parse_espn_date(item["startDate"]))
            end = central_date(parse_espn_date(item["endDate"]))
            events.append(Event(uid=f"golf-{item.get('id', label + str(start))}",
                                title=GOLF_EVENTS[label], start=start, end=end,
                                categories=["Golf"]))
    return events


def tennis():
    events = []
    for name, first, last in TENNIS_SLAMS:
        events.append(Event(uid=f"tennis-{name}-{first.year}".replace(" ", "-"),
                            title=name, start=first, end=last, categories=["Tennis"]))
        if not (first - timedelta(days=1) <= TODAY <= last + timedelta(days=1)):
            continue
        # During a Slam: add the matches from the round of 16 on.
        data = fetch_json(f"{ESPN}/tennis/atp/scoreboard?dates={TODAY:%Y%m%d}")
        for ev in data.get("events", []):
            if not ev.get("major"):
                continue
            for group in ev.get("groupings", []):
                if (group.get("grouping") or {}).get("displayName") != TENNIS_DRAW:
                    continue
                for comp in group.get("competitions", []):
                    rnd = (comp.get("round") or {}).get("displayName", "")
                    if rnd not in TENNIS_ROUNDS:
                        continue
                    players = [((c.get("athlete") or {}).get("displayName") or "TBD")
                               for c in comp.get("competitors", [])] or ["TBD", "TBD"]
                    round_name = "R16" if rnd == "Round 4" else rnd
                    title = f"{name.replace(' (Tennis)', '')} {round_name}: {' vs '.join(players)}"
                    start = parse_espn_date(comp["date"])
                    status = (comp.get("status") or {}).get("type") or {}
                    events.append(Event(uid=f"espn-tennis-{comp['id']}", title=title, start=start,
                                        description=status.get("detail", "") if status.get("completed") else "",
                                        duration_hours=3.0, categories=["Tennis"]))
    return events


def other_banners():
    return [Event(uid=f"banner-{name}-{first.year}".replace(" ", "-"), title=name,
                  start=first, end=last, location=where)
            for name, first, last, where in OTHER_BANNERS]


# =============================================================== build

def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "site"
    os.makedirs(out_dir, exist_ok=True)

    # Order matters: when the same game comes from two places, the first one wins
    # (e.g. an Astros ALCS game keeps its "ALCS Game 3" title, and a Texans
    # Thursday game is filed under the Texans rather than the NFL).
    sources = [mlb_postseason, pro_team_games, nfl_games, college_football,
               tcu_games, golf_banners, tennis, other_banners]
    cutoff = NOW - timedelta(days=KEEP_PAST_DAYS)
    seen, events = set(), []
    for source in sources:
        found = source()  # any failure stops the run, so yesterday's file stays up
        kept = 0
        for e in found:
            if e.uid in seen or e.last_moment() < cutoff:
                continue
            seen.add(e.uid)
            events.append(e)
            kept += 1
        print(f"{source.__name__:16} {kept:4} events")

    if len(events) < 50:
        raise SystemExit(f"Only {len(events)} events; something is wrong, not publishing.")
    write_ics(os.path.join(out_dir, "sports.ics"), events, CALENDAR_NAME,
              "TCU, Houston teams, NFL, college football, MLB playoffs, golf and tennis majors. Updates daily.")
    print(f"Wrote {len(events)} events to {out_dir}/sports.ics")


if __name__ == "__main__":
    main()
