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

# Pro teams: every regular-season, play-in and playoff game.
# (ESPN league path, team id, hours, local TV used when no national channel is listed)
PRO_TEAMS = {
    "Rockets": ("basketball/nba", 10, 2.5, "Space City Home Network (local)"),
    "Texans": ("football/nfl", 34, 3.5, ""),
    "Astros": ("baseball/mlb", 18, 3.0, "Space City Home Network (local)"),
}

# NBA regular season, beyond the Rockets:
# every game between these pairs of teams (ESPN team ids),
NBA_RIVALRIES = [
    (25, 24),   # Thunder - Spurs
    (24, 18),   # Spurs - Knicks
    (25, 18),   # Thunder - Knicks
    (20, 2),    # 76ers - Celtics
    (18, 20),   # Knicks - 76ers
    (2, 18),    # Celtics - Knicks
]
# and from January 1 on, every game between two teams that are currently
# top-6 seeds in their conference (re-checked every run as standings change).
NBA_TOP_SEEDS = 6
NBA_TOP_SEEDS_FROM = (1, 1)   # (month, day)
NBA_LOCAL_TV = "Local TV / League Pass"

# TV shown in place of the location. Out-of-market services are left out.
SKIP_TV = ("MLB.TV", "NBA League Pass", "NHL.TV", "NFL+")

# NFL: games not played on Sunday (Thursday, Monday, Saturday, holidays),
# an all-day "NFL Week N" banner on each Sunday, and all playoff games.
NFL_NON_SUNDAY_GAMES = True
NFL_WEEK_BANNERS = True
NFL_PLAYOFFS = True

# NBA and NHL: every play-in and playoff game, added as they're scheduled.
NBA_PLAYOFFS = True
NHL_PLAYOFFS = True

# Top 25 matchups: every remaining regular-season game where BOTH teams are in the
# current AP Top 25. Re-checked every run, so it follows each new poll.
CFB_TOP25_MATCHUPS = True
CBB_TOP25_MATCHUPS = True   # men's college basketball; starts once the season's first AP poll is out
# Men's college basketball in March: every NCAA tournament game, and these conferences'
# tournament games from the quarterfinals on, with matchups and times as they're set.
MARCH_MADNESS_GAMES = True
CBB_CONF_TOURNAMENTS = ("ACC", "Big 12", "Big East", "SEC", "Big Ten")
CBB_CONF_ROUNDS = ("Quarterfinal", "Semifinal", "Final")
COLLEGE_FOOTBALL_PLAYOFF = True
# Non-CFP bowl games where both teams are from a Power Four conference
# (ESPN conference ids: ACC 1, Big 12 4, Big Ten 5, SEC 8).
P4_BOWLS = True
P4_CONFERENCES = ("1", "4", "5", "8")
# Regular-season games between two Power Four teams played on any day but Saturday,
# up until Thanksgiving week (Black Friday is treated like a normal Saturday).
P4_NON_SATURDAY = True

# College games always included, ranked or not (ESPN team ids). TCU's own games
# come from TCU's feed, so TCU is left out here.
TEXAS_P4 = [2641, 2567, 239, 248, 245, 251]   # Texas Tech, SMU, Baylor, Houston, Texas A&M, Texas
CFB_RIVALRIES = [
    (251, 245),   # Texas - Texas A&M
    (130, 194),   # Michigan - Ohio State
] + [(a, b) for i, a in enumerate(TEXAS_P4) for b in TEXAS_P4[i + 1:]]   # Texas P4 vs each other
CBB_RIVALRIES = [
    (150, 153),   # Duke - North Carolina
]
# Power Four conference title games, always included.
CFB_TITLE_GAMES = ("ACC Championship", "Big 12 Championship", "Big Ten Championship", "SEC Championship")

# U.S. men's national team: every Gold Cup game, and Nations League Finals games
# (semifinal, final, 3rd place). (ESPN league, first day to check, last day, rounds or None = all)
USMNT_ID = "660"
USMNT_TOURNAMENTS = {
    "Gold Cup": ("concacaf.gold", (6, 1), (7, 20), None),
    "Nations League Finals": ("concacaf.nations.league", (3, 10), (4, 5),
                              ("semifinals", "final", "3rd-place-match")),
}

# College baseball: every Super Regional and College World Series game.
COLLEGE_BASEBALL_POSTSEASON = True

# MLB postseason: every game (Wild Card, Division Series, LCS, World Series).
MLB_PLAYOFFS = True
MLB_ROUND_NAMES = {"ALWC": "AL Wild Card", "NLWC": "NL Wild Card"}

# Golf: all-day banners for these events (read from ESPN's golf calendar).
# ESPN label: (title, TV)
GOLF_EVENTS = {
    "Masters Tournament": ("Masters", "ESPN Thu-Fri, CBS Sat-Sun"),
    "PGA Championship": ("PGA Championship", "ESPN Thu-Fri, CBS Sat-Sun"),
    "U.S. Open": ("U.S. Open (Golf)", "USA Thu-Fri, NBC Sat-Sun, Peacock"),
    "The Open": ("The Open Championship", "USA Thu-Fri, NBC Sat-Sun, Peacock"),
    "The Open Championship": ("The Open Championship", "USA Thu-Fri, NBC Sat-Sun, Peacock"),
    "Ryder Cup": ("Ryder Cup", "USA, NBC, Peacock"),
}

# Tennis: all-day banners for each Grand Slam from the first day of the men's
# round of 16 through the men's final, plus men's singles matches from those
# rounds. ESPN doesn't publish future Slam dates, so ADD NEXT YEAR'S DATES HERE
# once they're announced. (name, first day of men's R16, men's final, TV)
TENNIS_ROUNDS = ("Round 4", "Quarterfinal", "Semifinal", "Final")
TENNIS_DRAW = "Men's Singles"
TENNIS_SLAMS = [
    ("Australian Open", date(2027, 1, 24), date(2027, 1, 31), "ESPN, ESPN2, ESPN+"),
    ("French Open", date(2027, 5, 30), date(2027, 6, 6), "TNT, truTV, HBO Max"),
    ("Wimbledon", date(2027, 7, 4), date(2027, 7, 11), "ESPN, ESPN2, ESPN+"),
    ("US Open (Tennis)", date(2027, 9, 5), date(2027, 9, 12), "ESPN, ESPN2, ESPN+"),
]

# Other fixed all-day banners. ADD NEXT SEASON'S DATES HERE once they're announced.
# (name, first day, last day, notes)
OTHER_BANNERS = [
    # Soccer 2027
    ("Gold Cup", date(2027, 6, 17), date(2027, 7, 11), "CONCACAF Gold Cup"),
    # Drafts 2027
    ("NFL Draft Round 1", date(2027, 4, 29), None, "National Mall, Washington, D.C."),
    ("NFL Draft Rounds 2-3", date(2027, 4, 30), None, "National Mall, Washington, D.C."),
    ("NFL Draft Rounds 4-7", date(2027, 5, 1), None, "National Mall, Washington, D.C."),
    ("NBA Draft Round 1 (date not yet confirmed)", date(2027, 6, 24), None, ""),
    ("NBA Draft Round 2 (date not yet confirmed)", date(2027, 6, 25), None, ""),
    # March Madness 2027 (men's)
    ("March Madness: First Four", date(2027, 3, 16), date(2027, 3, 17), "Dayton, OH"),
    ("March Madness: Round of 64", date(2027, 3, 18), date(2027, 3, 19), ""),
    ("March Madness: Round of 32", date(2027, 3, 20), date(2027, 3, 21), ""),
    ("March Madness: Sweet 16", date(2027, 3, 25), date(2027, 3, 26), ""),
    ("March Madness: Elite 8", date(2027, 3, 27), date(2027, 3, 28), ""),
    ("March Madness: Final Four", date(2027, 4, 3), date(2027, 4, 3), "Ford Field, Detroit"),
    ("March Madness: National Championship", date(2027, 4, 5), date(2027, 4, 5), "Ford Field, Detroit"),
    # Men's conference tournaments 2027: quarterfinals, semifinals, final
    ("Big 12 Tournament: Quarterfinals", date(2027, 3, 11), None, "T-Mobile Center, Kansas City"),
    ("Big 12 Tournament: Semifinals", date(2027, 3, 12), None, "T-Mobile Center, Kansas City"),
    ("Big 12 Tournament: Championship", date(2027, 3, 13), None, "T-Mobile Center, Kansas City"),
    ("ACC Tournament: Quarterfinals", date(2027, 3, 11), None, "First Horizon Coliseum, Greensboro"),
    ("ACC Tournament: Semifinals", date(2027, 3, 12), None, "First Horizon Coliseum, Greensboro"),
    ("ACC Tournament: Championship", date(2027, 3, 13), None, "First Horizon Coliseum, Greensboro"),
    ("Big East Tournament: Quarterfinals", date(2027, 3, 11), None, "Madison Square Garden, New York"),
    ("Big East Tournament: Semifinals", date(2027, 3, 12), None, "Madison Square Garden, New York"),
    ("Big East Tournament: Championship", date(2027, 3, 13), None, "Madison Square Garden, New York"),
    ("SEC Tournament: Quarterfinals", date(2027, 3, 12), None, "Bridgestone Arena, Nashville"),
    ("SEC Tournament: Semifinals", date(2027, 3, 13), None, "Bridgestone Arena, Nashville"),
    ("SEC Tournament: Championship", date(2027, 3, 14), None, "Bridgestone Arena, Nashville"),
    ("Big Ten Tournament: Quarterfinals", date(2027, 3, 12), None, "Gainbridge Fieldhouse, Indianapolis"),
    ("Big Ten Tournament: Semifinals", date(2027, 3, 13), None, "Gainbridge Fieldhouse, Indianapolis"),
    ("Big Ten Tournament: Championship", date(2027, 3, 14), None, "Gainbridge Fieldhouse, Indianapolis"),
]

# Emoji at the front of each event title, by category.
EMOJI = {
    "TCU Football": "\U0001F438\U0001F3C8",     # frog + ball
    "TCU Basketball": "\U0001F438\U0001F3C0",
    "TCU Baseball": "\U0001F438⚾",
    "Rockets": "\U0001F680",            # rocket
    "Texans": "\U0001F402",             # bull
    "Astros": "⭐",                 # star
    "NFL": "\U0001F3C8",                # football
    "NBA": "\U0001F3C0",                # basketball
    "Top 25": "\U0001F393\U0001F3C8",   # graduation cap = college
    "Conference Title": "\U0001F393\U0001F3C8",
    "Bowl": "\U0001F393\U0001F3C8",
    "CFP": "\U0001F393\U0001F3C6",      # graduation cap + trophy
    "NFL Playoffs": "\U0001F3C8\U0001F3C5",    # ball + medal = pro playoffs
    "MLB Playoffs": "⚾\U0001F3C5",
    "NBA Playoffs": "\U0001F3C0\U0001F3C5",
    "NHL Playoffs": "\U0001F3D2\U0001F3C5",    # hockey stick + medal
    "CBB Top 25": "\U0001F393\U0001F3C0",
    "Conf Tournament": "\U0001F393\U0001F3C0",
    "March Madness": "\U0001F393\U0001F3C0",
    "College Baseball": "\U0001F393⚾",   # graduation cap + baseball
    "USMNT": "⚽\U0001F1FA\U0001F1F8",     # soccer ball + US flag
    "Golf": "⛳",                   # flag in hole
    "Tennis": "\U0001F3BE",             # tennis
    "Olympics": "\U0001F3C5",           # medal
}

# Drop events that ended more than this many days ago
# (1 = a 10/14 game is still there on 10/15 and gone on 10/16).
KEEP_PAST_DAYS = 1

# =============================================================== sources

ESPN = "https://site.api.espn.com/apis/site/v2/sports"
# TCU's ESPN ids (football/basketball, baseball); its games come from TCU's own feed
TCU_ESPN_IDS = ("2628", "198")
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
            title = f"TCU {opponent}"   # the emoji shows the sport
            start = parse_ics_datetime(raw["DTSTART"])
            if not isinstance(start, datetime):
                title += " (time TBA)"
            # Lines after the first are the result, TV, streaming links and radio
            lines = [l.strip() for l in raw.get("DESCRIPTION", "").split("\n")[1:]]
            tv = [l[3:].strip() for l in lines if l.startswith("TV:")]
            radio = [l for l in lines if l.startswith("Radio:")]
            notes = [promo, raw.get("LOCATION", "").strip()] + radio
            events.append(Event(
                uid=f"tcu-{raw.get('UID', title + str(start))}",
                title=title,
                start=start,
                location=tv_label(tv),
                description="\n".join(n for n in notes if n),
                url=raw.get("URL", "").replace("&amp;", "&"),
                duration_hours=3.5 if "Football" in label else 3.0 if "Baseball" in label else 2.5,
                categories=[label],
            ))
    return events


def tv_label(channels):
    """What goes in the event's location line: "📺 ESPN · ESPN+" or "📺 TV TBA"."""
    channels = [c for c in dict.fromkeys(channels) if c and c not in SKIP_TV]
    return "\U0001F4FA " + (" · ".join(channels) if channels else "TV TBA")


def espn_tv(comp, local_tv=""):
    """National channels for an ESPN game. When there are none, the team's
    local channel (e.g. Space City Home Network for the Rockets and Astros)."""
    national = []
    for b in (comp.get("geoBroadcasts") or []) + (comp.get("broadcasts") or []):
        market = b.get("market")
        market = (market.get("type") if isinstance(market, dict) else market) or ""
        kind = b.get("type")
        kind = (kind.get("shortName") if isinstance(kind, dict) else kind) or ""
        if market.lower() != "national" or kind.lower() == "radio":
            continue
        national += b.get("names") or [((b.get("media") or {}).get("shortName"))]
    national = [c for c in national if c and c not in SKIP_TV]
    if national:
        return tv_label(national)
    return tv_label([local_tv] if local_tv else [])


def _team_name(competitor):
    team = competitor.get("team") or {}
    name = team.get("shortDisplayName") or team.get("displayName") or "TBD"
    return "TBD" if "TBD" in name else name


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


def espn_event(ev, league, title=None, hours=3.0, category="", rank=False, use_location=False,
               local_tv=""):
    """Turns an ESPN event (from a schedule or scoreboard) into a calendar Event.
    The TV channel goes where the location usually shows; the venue goes in the notes."""
    comp = ev["competitions"][0]
    matchup = _matchup(comp, rank, use_location)[0]
    title = title or matchup
    status = comp.get("status") or ev.get("status") or {}
    state = (status.get("type") or {}).get("name", "")
    if state in ("STATUS_POSTPONED", "STATUS_CANCELED"):
        title += " (Postponed)" if state == "STATUS_POSTPONED" else " (Canceled)"

    start = parse_espn_date(ev["date"])
    if comp.get("timeValid", True) is False:
        start = game_day(ev)
        title += " (time TBA)"

    venue = comp.get("venue") or {}
    addr = venue.get("address") or {}
    where = ", ".join(x for x in (venue.get("fullName"), addr.get("city"), addr.get("state")) if x)

    return Event(
        uid=f"espn-{league.replace('/', '-')}-{ev['id']}",
        title=title,
        start=start,
        location=espn_tv(comp, local_tv),
        description=where,
        duration_hours=hours,
        categories=[category] if category else [],
    )


def _note(comp):
    return " ".join(n.get("headline", "") for n in comp.get("notes") or []).strip()


def pro_team_games():
    events = []
    for name, (league, team_id, hours, local_tv) in PRO_TEAMS.items():
        # Ask for this season and next, so the calendar rolls into new seasons on its own.
        for season in (TODAY.year, TODAY.year + 1):
            season_types = (2, 3, 5) if league.endswith("nba") else (2, 3)  # 5 = NBA play-in
            for season_type in season_types:
                url = f"{ESPN}/{league}/teams/{team_id}/schedule?season={season}&seasontype={season_type}"
                try:
                    data = fetch_json(url)
                except RuntimeError:
                    if season == TODAY.year + 1 or season_type != 2:
                        continue  # next season or playoffs not published yet
                    raise
                for ev in data.get("events", []):
                    note = _note(ev["competitions"][0])
                    title = None
                    if note and (season_type in (3, 5) or "Cup" in note):
                        title = f"{espn_matchup_title(ev)} ({note})"
                    events.append(espn_event(ev, league, title, hours, name, local_tv=local_tv))
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
                events.append(espn_event(ev, "football/nfl", title, 3.5, "NFL Playoffs"))
    return events


def _involves_tcu(ev):
    return any((c.get("team") or {}).get("id") in TCU_ESPN_IDS
               for c in ev["competitions"][0].get("competitors", []))


def top25_matchups(league, season, label, hours, rivalries=()):
    """Every remaining regular-season game between two teams in the current AP Top 25,
    plus every game between the rivalry pairs, ranked or not. Reads each team's
    schedule, so it covers the rest of the season."""
    polls = fetch_json(f"{ESPN}/{league}/rankings").get("rankings", [])
    ap = next((p for p in polls if p.get("name") == "AP Top 25"), None)
    ranks = {}
    if ap and (ap.get("season") or {}).get("year") == season:  # this season's poll is out
        ranks = {r["team"]["id"]: r["current"] for r in ap.get("ranks", []) if r.get("current", 99) <= 25}
    rivalries = {frozenset((str(a), str(b))) for a, b in rivalries}
    teams = set(ranks) | {t for pair in rivalries for t in pair}
    events, seen = [], set()
    for team_id in sorted(teams):
        data = fetch_json(f"{ESPN}/{league}/teams/{team_id}/schedule?season={season}&seasontype=2")
        for ev in data.get("events", []):
            comp = ev["competitions"][0]
            ids = [(c.get("team") or {}).get("id") for c in comp.get("competitors", [])]
            wanted = all(i in ranks for i in ids) or frozenset(ids) in rivalries
            if ev["id"] in seen or not wanted or _involves_tcu(ev):
                continue
            if game_day(ev) < TODAY:
                continue
            seen.add(ev["id"])
            for c in comp["competitors"]:  # show this week's poll ranks
                c["curatedRank"] = {"current": ranks.get(c["team"]["id"], 99)}
            events.append(espn_event(ev, league, hours=hours, category=label,
                                     rank=True, use_location=True))
    return events


def p4_non_saturday(league):
    """Remaining regular-season Power Four vs Power Four games not played on a Saturday."""
    events = []
    current = fetch_json(f"{ESPN}/{league}/scoreboard?groups=80&limit=300")
    calendar = (current.get("leagues") or [{}])[0].get("calendar", [])
    regular = next((c for c in calendar if c.get("label") == "Regular Season"), {})
    weeks = [int(w["value"]) for w in regular.get("entries", [])]
    this_week = (current.get("week") or {}).get("number", 1)
    if (current.get("season") or {}).get("type") != 2:
        return events  # regular season is over
    polls = fetch_json(f"{ESPN}/{league}/rankings").get("rankings", [])
    ap = next((p for p in polls if p.get("name") == "AP Top 25"), {})
    ranks = {r["team"]["id"]: r["current"] for r in ap.get("ranks", []) if r.get("current", 99) <= 25}
    # Thanksgiving is the 4th Thursday of November; stop at the Monday of that week.
    nov1 = date(nfl_season(), 11, 1)
    thanksgiving = nov1 + timedelta(days=(3 - nov1.weekday()) % 7 + 21)
    cutoff = thanksgiving - timedelta(days=3)
    for week in [w for w in weeks if w >= this_week]:
        data = fetch_json(f"{ESPN}/{league}/scoreboard?seasontype=2&week={week}"
                          f"&dates={nfl_season()}&groups=80&limit=300")
        for ev in data.get("events", []):
            comp = ev["competitions"][0]
            confs = [(c.get("team") or {}).get("conferenceId") for c in comp.get("competitors", [])]
            day = game_day(ev)
            if (day.weekday() == 5 or day < TODAY or day >= cutoff or len(confs) != 2
                    or not all(c in P4_CONFERENCES for c in confs) or _involves_tcu(ev)):
                continue
            for c in comp["competitors"]:  # this week's AP ranks
                c["curatedRank"] = {"current": ranks.get(c["team"]["id"], 99)}
            events.append(espn_event(ev, league, hours=3.5, category="Top 25",
                                     rank=True, use_location=True))
    return events


def college_football():
    events = []
    league = "football/college-football"
    if CFB_TITLE_GAMES:
        # Conference title games are in the last regular-season weeks.
        calendar = (fetch_json(f"{ESPN}/{league}/scoreboard").get("leagues") or [{}])[0].get("calendar", [])
        regular = next((c for c in calendar if c.get("label") == "Regular Season"), {})
        last_weeks = [int(w["value"]) for w in regular.get("entries", [])][-2:]
        for week in last_weeks:
            data = fetch_json(f"{ESPN}/{league}/scoreboard?seasontype=2&week={week}"
                              f"&dates={nfl_season()}&groups=80&limit=300")
            for ev in data.get("events", []):
                note = _note(ev["competitions"][0])
                if note in CFB_TITLE_GAMES and not _involves_tcu(ev):
                    matchup = _matchup(ev["competitions"][0], rank=True, use_location=True)[0]
                    title = note if "TBD" in matchup else f"{note}: {matchup}"
                    events.append(espn_event(ev, league, title, 3.5, "Conference Title"))
    if CFB_TOP25_MATCHUPS:
        events += top25_matchups(league, nfl_season(), "Top 25", 3.5, CFB_RIVALRIES)
    if P4_NON_SATURDAY:
        events += p4_non_saturday(league)
    if COLLEGE_FOOTBALL_PLAYOFF or P4_BOWLS:
        data = fetch_json(f"{ESPN}/{league}/scoreboard?seasontype=3&week=1&dates={nfl_season()}&limit=100")
        for ev in data.get("events", []):
            comp = ev["competitions"][0]
            note = _note(comp)
            if "College Football Playoff" not in note:
                # Other bowls: only when both teams are set and both are Power Four
                confs = [(c.get("team") or {}).get("conferenceId") for c in comp.get("competitors", [])]
                if (P4_BOWLS and "Bowl" in note and len(confs) == 2
                        and all(c in P4_CONFERENCES for c in confs) and not _involves_tcu(ev)):
                    bowl = re.sub(r"\s+Presented by.*$", "", note, flags=re.I)
                    matchup = _matchup(comp, rank=True, use_location=True)[0].replace(" @ ", " vs ")
                    events.append(espn_event(ev, league, f"{bowl}: {matchup}", 3.5, "Bowl"))
                continue
            if not COLLEGE_FOOTBALL_PLAYOFF:
                continue
            note = (note.replace("College Football Playoff", "CFP")
                        .replace(" at the ", " – "))
            note = re.sub(r"\s+Presented by.*$", "", note, flags=re.I)
            matchup = _matchup(ev["competitions"][0], rank=False, use_location=True)[0]
            title = note if "TBD" in matchup else f"{note}: {matchup}"
            events.append(espn_event(ev, league, title, 3.5, "CFP"))
    return events


def _scoreboard_days(league, first, last, extra=""):
    """Day-by-day scoreboards (ESPN has no date-range query for these leagues),
    only while today is within a month of the window."""
    if not (first - timedelta(days=30) <= TODAY <= last):
        return
    day = max(first, TODAY - timedelta(days=KEEP_PAST_DAYS))
    while day <= last:
        yield from fetch_json(f"{ESPN}/{league}/scoreboard?dates={day:%Y%m%d}{extra}").get("events", [])
        day += timedelta(days=1)


def mlb_postseason():
    events = []
    if not MLB_PLAYOFFS:
        return events
    year = TODAY.year
    for ev in _scoreboard_days("baseball/mlb", date(year, 9, 25), date(year, 11, 10)):
        if (ev.get("season") or {}).get("type") != 3:  # postseason only
            continue
        note = _note(ev["competitions"][0]) or "MLB Playoffs"
        # "World Series - Game 6 If Necessary" -> "World Series Game 6 (if necessary)"
        label = note.replace(" - ", " ").replace(" If Necessary", " (if necessary)")
        for short, full in MLB_ROUND_NAMES.items():
            label = label.replace(short, full)
        matchup = espn_matchup_title(ev)
        title = label if _teams_unknown(matchup) else f"{label}: {matchup}"
        events.append(espn_event(ev, "baseball/mlb", title, 3.0, "MLB Playoffs"))
    return events


def usmnt():
    """U.S. men's national team games in the tournaments above, e.g.
    "Gold Cup Final: USA vs Mexico"."""
    events = []
    year = TODAY.year
    for name, (league, first, last, rounds) in USMNT_TOURNAMENTS.items():
        for ev in _scoreboard_days(f"soccer/{league}", date(year, *first), date(year, *last)):
            comp = ev["competitions"][0]
            teams = {(c.get("team") or {}).get("id"): c for c in comp.get("competitors", [])}
            if USMNT_ID not in teams:
                continue
            stage = (ev.get("season") or {}).get("slug", "")
            if rounds and stage not in rounds:
                continue
            opponent = next(((c.get("team") or {}).get("displayName") or "TBD"
                             for i, c in teams.items() if i != USMNT_ID), "TBD")
            # "semifinals" -> "Semifinal", "3rd-place-match" -> "3rd Place Match"
            round_name = stage.replace("-", " ").title().replace("3Rd", "3rd").replace("finals", "final")
            title = f"{name} {round_name}: USA vs {opponent}".replace(f"{name} :", f"{name}:")
            events.append(espn_event(ev, f"soccer/{league}", title, 2.0, "USMNT"))
    return events


def college_baseball_postseason():
    """Every Super Regional and Men's College World Series game."""
    events = []
    if not COLLEGE_BASEBALL_POSTSEASON:
        return events
    league = "baseball/college-baseball"
    year = TODAY.year
    for ev in _scoreboard_days(league, date(year, 6, 1), date(year, 6, 30), "&limit=200"):
        if _involves_tcu(ev):
            continue  # TCU's own feed has these
        note = _note(ev["competitions"][0])
        # "NCAA Baseball Championship - Austin Super Regional - Game 1"
        # (but not "... - Atlanta Regional - Oklahoma advances to Super Regional")
        parts = note.split(" - ")[1:]
        if parts and parts[0].endswith("Super Regional"):
            label = " ".join(parts)
        elif "College World Series" in note:
            # "Men's College World Series Championship Final - Game 1" -> "CWS Championship Final Game 1"
            label = note.replace("Men's College World Series", "CWS").replace(" - ", " ")
        else:
            continue
        # rank=True shows national seeds (#1-16), e.g. "USC @ #5 North Carolina"
        matchup = _matchup(ev["competitions"][0], rank=True, use_location=True)[0]
        title = label if "TBD" in matchup else f"{label}: {matchup}"
        events.append(espn_event(ev, league, title, 3.0, "College Baseball"))
    return events


def nba_seeds(season):
    """{team id: "#1W"} for the current top seeds in each conference."""
    data = fetch_json(f"https://site.api.espn.com/apis/v2/sports/basketball/nba/standings?season={season}")
    seeds = {}
    for conf in data.get("children", []):
        letter = "E" if "East" in conf.get("name", "") else "W"
        for row in (conf.get("standings") or {}).get("entries", []):
            seed = next((s.get("value") for s in row.get("stats", []) if s.get("name") == "playoffSeed"), None)
            if seed and int(seed) <= NBA_TOP_SEEDS:
                seeds[row["team"]["id"]] = f"#{int(seed)}{letter}"
    return seeds


def nba_regular_season():
    league = "basketball/nba"
    season = TODAY.year + 1 if TODAY.month >= 7 else TODAY.year   # 2026-27 season = 2027
    seeds_from = date(season, *NBA_TOP_SEEDS_FROM)
    seeds = nba_seeds(season) if TODAY >= seeds_from else {}
    rivalries = {frozenset((str(a), str(b))) for a, b in NBA_RIVALRIES}
    teams = {t for pair in rivalries for t in pair} | set(seeds)

    events, seen = [], set()
    for team_id in sorted(teams):
        data = fetch_json(f"{ESPN}/{league}/teams/{team_id}/schedule?season={season}&seasontype=2")
        for ev in data.get("events", []):
            comp = ev["competitions"][0]
            ids = [(c.get("team") or {}).get("id") for c in comp.get("competitors", [])]
            top_matchup = all(i in seeds for i in ids) and game_day(ev) >= seeds_from
            if ev["id"] in seen or not (frozenset(ids) in rivalries or top_matchup):
                continue
            seen.add(ev["id"])
            teams_by_side = {c.get("homeAway"): c for c in comp["competitors"]}

            def name(c):
                tid = (c.get("team") or {}).get("id")
                return f"{seeds[tid]} {_team_name(c)}" if tid in seeds else _team_name(c)

            sep = "vs" if comp.get("neutralSite") else "@"
            title = f"{name(teams_by_side['away'])} {sep} {name(teams_by_side['home'])}"
            events.append(espn_event(ev, league, title, 2.5, "NBA", local_tv=NBA_LOCAL_TV))
    return events


def nhl_postseason():
    events = []
    if not NHL_PLAYOFFS:
        return events
    year = TODAY.year
    for ev in _scoreboard_days("hockey/nhl", date(year, 4, 12), date(year, 6, 25)):
        if (ev.get("season") or {}).get("type") != 3:
            continue
        # "East 1st Round - Game 2" -> "East 1st Round Game 2"
        label = _note(ev["competitions"][0]).replace(" - ", " ") or "NHL Playoffs"
        matchup = espn_matchup_title(ev)
        title = label if _teams_unknown(matchup) else f"{label}: {matchup}"
        events.append(espn_event(ev, "hockey/nhl", title, 2.5, "NHL Playoffs"))
    return events


def nba_postseason():
    events = []
    if not NBA_PLAYOFFS:
        return events
    year = TODAY.year
    for ev in _scoreboard_days("basketball/nba", date(year, 4, 10), date(year, 6, 25)):
        if (ev.get("season") or {}).get("type") not in (3, 5):  # playoffs, play-in
            continue
        # "East 1st Round - Game 1" -> "East 1st Round Game 1"
        label = _note(ev["competitions"][0]).replace("NBA Play-In - ", "Play-In ").replace(" - ", " ")
        label = label or "NBA Playoffs"
        matchup = espn_matchup_title(ev)
        title = label if _teams_unknown(matchup) else f"{label}: {matchup}"
        events.append(espn_event(ev, "basketball/nba", title, 2.5, "NBA Playoffs"))
    return events


NCAA_ROUNDS = {"1st Round": "Round of 64", "2nd Round": "Round of 32"}


def march_basketball():
    """Conference tournaments (quarterfinals on) and every NCAA tournament game."""
    events = []
    league = "basketball/mens-college-basketball"
    conf_pattern = re.compile(r"\b(%s) Tournament - (%s)$" % (
        "|".join(map(re.escape, CBB_CONF_TOURNAMENTS)), "|".join(CBB_CONF_ROUNDS)))
    year = TODAY.year
    for ev in _scoreboard_days(league, date(year, 3, 1), date(year, 4, 10), "&groups=50&limit=400"):
        if _involves_tcu(ev):
            continue  # TCU's own feed has these
        comp = ev["competitions"][0]
        note = _note(comp)
        matchup = _matchup(comp, rank=True, use_location=True)[0]
        conf = conf_pattern.search(note)
        if conf:
            label, category = f"{conf.group(1)} Tournament {conf.group(2)}", "Conf Tournament"
        elif MARCH_MADNESS_GAMES and note.startswith("NCAA Men's Basketball Championship"):
            # "NCAA Men's Basketball Championship - South Region - 1st Round"
            parts = note.split(" - ")[1:]
            rnd = NCAA_ROUNDS.get(parts[-1], parts[-1])
            region = f" ({parts[0].replace(' Region', '')})" if len(parts) > 1 else ""
            label, category = f"March Madness {rnd}{region}", "March Madness"
        else:
            continue
        title = label if "TBD" in matchup else f"{label}: {matchup}"
        events.append(espn_event(ev, league, title, 2.0, category))
    return events


def college_basketball():
    if not CBB_TOP25_MATCHUPS:
        return []
    season = TODAY.year + 1 if TODAY.month >= 7 else TODAY.year  # 2026-27 season = 2027
    return top25_matchups("basketball/mens-college-basketball", season, "CBB Top 25", 2.0, CBB_RIVALRIES)


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
                                title=GOLF_EVENTS[label][0], start=start, end=end,
                                location=tv_label([GOLF_EVENTS[label][1]]), categories=["Golf"]))
    return events


def tennis():
    events = []
    for name, first, last, tv in TENNIS_SLAMS:
        events.append(Event(uid=f"tennis-{name}-{first.year}".replace(" ", "-"),
                            title=name, start=first, end=last, location=tv_label([tv]),
                            categories=["Tennis"]))
        # Round-of-16 pairings and times get posted a day or two ahead.
        if not (first - timedelta(days=3) <= TODAY <= last + timedelta(days=1)):
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
                                        location=tv_label([tv]), duration_hours=3.0,
                                        categories=["Tennis"]))
    return events


def other_banners():
    def category(name):
        for key, cat in (("NFL Draft", "NFL"), ("NBA Draft", "NBA"), ("March Madness", "March Madness"),
                         ("Gold Cup", "USMNT")):
            if key in name:
                return cat
        return "Conf Tournament"

    tv = {"NFL": "ESPN · ABC · NFL Network", "NBA": "ABC · ESPN"}
    return [Event(uid=f"banner-{name.split(' (')[0]}-{first.year}".replace(" ", "-"), title=name,
                  start=first, end=last, description=where,
                  location=tv_label([tv[category(name)]]) if category(name) in tv else "",
                  categories=[category(name)])
            for name, first, last, where in OTHER_BANNERS]


# =============================================================== build

def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "site"
    os.makedirs(out_dir, exist_ok=True)

    # Order matters: when the same game comes from two places, the first one wins
    # (e.g. an Astros ALCS game keeps its "ALCS Game 3" title, and a Texans
    # Thursday game is filed under the Texans rather than the NFL).
    sources = [mlb_postseason, nba_postseason, nhl_postseason, pro_team_games, nba_regular_season, nfl_games, college_football,
               march_basketball, college_basketball, college_baseball_postseason, usmnt, tcu_games, golf_banners, tennis, other_banners]
    cutoff = NOW - timedelta(days=KEEP_PAST_DAYS)
    seen, events = set(), []
    for source in sources:
        found = source()  # any failure stops the run, so yesterday's file stays up
        kept = 0
        for e in found:
            if e.uid in seen or e.last_moment() < cutoff:
                continue
            seen.add(e.uid)
            emoji = EMOJI.get(e.categories[0]) if e.categories else None
            if emoji:
                e.title = f"{emoji} {e.title}"
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
