from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    country TEXT
);

CREATE TABLE IF NOT EXISTS fixtures (
    id INTEGER PRIMARY KEY,
    league_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    round TEXT,
    matchday INTEGER,
    kickoff_utc TEXT NOT NULL,
    status TEXT,
    venue TEXT,
    referee TEXT,
    home_team_id INTEGER NOT NULL REFERENCES teams(id),
    away_team_id INTEGER NOT NULL REFERENCES teams(id),
    home_goals INTEGER,
    away_goals INTEGER,
    home_goals_ht INTEGER,
    away_goals_ht INTEGER
);

CREATE INDEX IF NOT EXISTS idx_fixtures_league_season ON fixtures(league_id, season);

CREATE TABLE IF NOT EXISTS fixture_stats (
    fixture_id INTEGER NOT NULL REFERENCES fixtures(id),
    team_id INTEGER NOT NULL REFERENCES teams(id),
    shots_total INTEGER,
    shots_on_target INTEGER,
    possession_pct REAL,
    corners INTEGER,
    fouls INTEGER,
    yellow_cards INTEGER,
    red_cards INTEGER,
    offsides INTEGER,
    passes_total INTEGER,
    passes_accurate INTEGER,
    saves INTEGER,
    expected_goals REAL,
    PRIMARY KEY (fixture_id, team_id)
);

CREATE TABLE IF NOT EXISTS standings_snapshots (
    league_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    matchday INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    played INTEGER NOT NULL,
    won INTEGER NOT NULL,
    drawn INTEGER NOT NULL,
    lost INTEGER NOT NULL,
    goals_for INTEGER NOT NULL,
    goals_against INTEGER NOT NULL,
    goal_diff INTEGER NOT NULL,
    points INTEGER NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (league_id, season, matchday, team_id)
);

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    firstname TEXT,
    lastname TEXT,
    birth_date TEXT,
    birth_country TEXT,
    nationality TEXT,
    height_cm INTEGER,
    weight_kg INTEGER
);

CREATE TABLE IF NOT EXISTS player_season_stats (
    player_id INTEGER NOT NULL REFERENCES players(id),
    team_id INTEGER NOT NULL REFERENCES teams(id),
    league_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    position TEXT,
    appearances INTEGER,
    lineups INTEGER,
    minutes INTEGER,
    rating REAL,
    shots_total INTEGER,
    shots_on INTEGER,
    goals INTEGER,
    assists INTEGER,
    saves INTEGER,
    passes_total INTEGER,
    passes_key INTEGER,
    tackles_total INTEGER,
    blocks INTEGER,
    interceptions INTEGER,
    duels_total INTEGER,
    duels_won INTEGER,
    dribbles_attempts INTEGER,
    dribbles_success INTEGER,
    fouls_drawn INTEGER,
    fouls_committed INTEGER,
    cards_yellow INTEGER,
    cards_red INTEGER,
    penalty_scored INTEGER,
    penalty_missed INTEGER,
    PRIMARY KEY (player_id, team_id, league_id, season)
);

CREATE INDEX IF NOT EXISTS idx_player_season_stats_team_season
    ON player_season_stats(team_id, league_id, season);

CREATE TABLE IF NOT EXISTS team_season_stats (
    league_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    form TEXT,
    played_total INTEGER,
    wins_home INTEGER,
    wins_away INTEGER,
    wins_total INTEGER,
    draws_home INTEGER,
    draws_away INTEGER,
    draws_total INTEGER,
    loses_home INTEGER,
    loses_away INTEGER,
    loses_total INTEGER,
    goals_for_home INTEGER,
    goals_for_away INTEGER,
    goals_for_total INTEGER,
    goals_against_home INTEGER,
    goals_against_away INTEGER,
    goals_against_total INTEGER,
    clean_sheets_home INTEGER,
    clean_sheets_away INTEGER,
    clean_sheets_total INTEGER,
    failed_to_score_home INTEGER,
    failed_to_score_away INTEGER,
    failed_to_score_total INTEGER,
    penalty_scored_total INTEGER,
    penalty_missed_total INTEGER,
    biggest_win_streak INTEGER,
    biggest_loss_streak INTEGER,
    goals_for_minutes_json TEXT,
    goals_for_under_over_json TEXT,
    goals_against_minutes_json TEXT,
    goals_against_under_over_json TEXT,
    PRIMARY KEY (league_id, season, team_id)
);

CREATE TABLE IF NOT EXISTS fixture_importance (
    fixture_id INTEGER PRIMARY KEY REFERENCES fixtures(id),
    importance REAL NOT NULL,
    is_derby INTEGER NOT NULL DEFAULT 0,
    title_component REAL NOT NULL,
    europe_component REAL NOT NULL,
    relegation_component REAL NOT NULL,
    season_weight REAL NOT NULL,
    home_position_before INTEGER,
    away_position_before INTEGER
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _matchday_from_round(round_label: str | None) -> int | None:
    if not round_label:
        return None
    match = re.search(r"(\d+)\s*$", round_label)
    return int(match.group(1)) if match else None


def upsert_team(conn: sqlite3.Connection, *, team_id: int, name: str, country: str | None) -> None:
    conn.execute(
        """
        INSERT INTO teams (id, name, country) VALUES (?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET name = excluded.name, country = excluded.country
        """,
        (team_id, name, country),
    )


def store_fixtures_payload(conn: sqlite3.Connection, payload: dict[str, Any], *, league_id: int, season: int) -> int:
    """Normalize and store API-Football /fixtures response items. Returns count stored."""
    stored = 0
    for item in payload.get("response", []):
        fixture = item.get("fixture", {})
        league = item.get("league", {})
        teams = item.get("teams", {})
        goals = item.get("goals", {})
        score = item.get("score", {})
        halftime = score.get("halftime", {}) or {}
        home_team = teams.get("home", {})
        away_team = teams.get("away", {})

        upsert_team(conn, team_id=home_team.get("id"), name=home_team.get("name", ""), country=league.get("country"))
        upsert_team(conn, team_id=away_team.get("id"), name=away_team.get("name", ""), country=league.get("country"))

        round_label = league.get("round")
        venue = fixture.get("venue") or {}
        conn.execute(
            """
            INSERT INTO fixtures (
                id, league_id, season, round, matchday, kickoff_utc, status, venue, referee,
                home_team_id, away_team_id, home_goals, away_goals, home_goals_ht, away_goals_ht
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                round = excluded.round,
                matchday = excluded.matchday,
                kickoff_utc = excluded.kickoff_utc,
                status = excluded.status,
                venue = excluded.venue,
                referee = excluded.referee,
                home_goals = excluded.home_goals,
                away_goals = excluded.away_goals,
                home_goals_ht = excluded.home_goals_ht,
                away_goals_ht = excluded.away_goals_ht
            """,
            (
                fixture.get("id"),
                league_id,
                season,
                round_label,
                _matchday_from_round(round_label),
                fixture.get("date"),
                (fixture.get("status") or {}).get("short"),
                venue.get("name"),
                fixture.get("referee"),
                home_team.get("id"),
                away_team.get("id"),
                goals.get("home"),
                goals.get("away"),
                halftime.get("home"),
                halftime.get("away"),
            ),
        )
        stored += 1
    conn.commit()
    return stored


_STAT_FIELD_MAP = {
    "Total Shots": "shots_total",
    "Shots on Goal": "shots_on_target",
    "Ball Possession": "possession_pct",
    "Corner Kicks": "corners",
    "Fouls": "fouls",
    "Yellow Cards": "yellow_cards",
    "Red Cards": "red_cards",
    "Offsides": "offsides",
    "Total passes": "passes_total",
    "Passes accurate": "passes_accurate",
    "Goalkeeper Saves": "saves",
    "expected_goals": "expected_goals",
}


def _clean_stat_value(raw: Any) -> float | int | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.strip().rstrip("%")
        if not raw:
            return None
        try:
            return float(raw) if "." in raw else int(raw)
        except ValueError:
            return None
    return raw


def store_fixture_statistics_payload(conn: sqlite3.Connection, payload: dict[str, Any], *, fixture_id: int) -> int:
    """Normalize and store API-Football /fixtures/statistics response items. Returns team-rows stored."""
    stored = 0
    for team_block in payload.get("response", []):
        team = team_block.get("team", {})
        team_id = team.get("id")
        if team_id is None:
            continue
        fields: dict[str, Any] = {}
        for stat in team_block.get("statistics", []):
            column = _STAT_FIELD_MAP.get(stat.get("type"))
            if column:
                fields[column] = _clean_stat_value(stat.get("value"))
        conn.execute(
            """
            INSERT INTO fixture_stats (
                fixture_id, team_id, shots_total, shots_on_target, possession_pct, corners,
                fouls, yellow_cards, red_cards, offsides, passes_total, passes_accurate, saves, expected_goals
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fixture_id, team_id) DO UPDATE SET
                shots_total = excluded.shots_total,
                shots_on_target = excluded.shots_on_target,
                possession_pct = excluded.possession_pct,
                corners = excluded.corners,
                fouls = excluded.fouls,
                yellow_cards = excluded.yellow_cards,
                red_cards = excluded.red_cards,
                offsides = excluded.offsides,
                passes_total = excluded.passes_total,
                passes_accurate = excluded.passes_accurate,
                saves = excluded.saves,
                expected_goals = excluded.expected_goals
            """,
            (
                fixture_id,
                team_id,
                fields.get("shots_total"),
                fields.get("shots_on_target"),
                fields.get("possession_pct"),
                fields.get("corners"),
                fields.get("fouls"),
                fields.get("yellow_cards"),
                fields.get("red_cards"),
                fields.get("offsides"),
                fields.get("passes_total"),
                fields.get("passes_accurate"),
                fields.get("saves"),
                fields.get("expected_goals"),
            ),
        )
        stored += 1
    conn.commit()
    return stored


def fetch_season_fixtures(conn: sqlite3.Connection, *, league_id: int, season: int) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        """
        SELECT * FROM fixtures
        WHERE league_id = ? AND season = ? AND matchday IS NOT NULL
        ORDER BY matchday ASC, kickoff_utc ASC
        """,
        (league_id, season),
    )
    return cursor.fetchall()


def fetch_fixtures_missing_stats(conn: sqlite3.Connection, *, league_id: int, season: int) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        """
        SELECT f.* FROM fixtures f
        WHERE f.league_id = ? AND f.season = ? AND f.status = 'FT'
          AND NOT EXISTS (SELECT 1 FROM fixture_stats s WHERE s.fixture_id = f.id)
        ORDER BY f.matchday ASC, f.kickoff_utc ASC
        """,
        (league_id, season),
    )
    return cursor.fetchall()


def fetch_team_names(conn: sqlite3.Connection) -> dict[int, str]:
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT id, name FROM teams")
    return {row["id"]: row["name"] for row in cursor.fetchall()}


def fetch_teams_for_season(conn: sqlite3.Connection, *, league_id: int, season: int) -> list[int]:
    """Distinct team ids that played in this league/season, derived from stored fixtures.

    Avoids spending API requests to rediscover a season's clubs - we already
    know them from the fixtures backfill.
    """
    cursor = conn.execute(
        """
        SELECT DISTINCT team_id FROM (
            SELECT home_team_id AS team_id FROM fixtures WHERE league_id = ? AND season = ?
            UNION
            SELECT away_team_id AS team_id FROM fixtures WHERE league_id = ? AND season = ?
        )
        """,
        (league_id, season, league_id, season),
    )
    return [row[0] for row in cursor.fetchall()]


def fetch_teams_with_season_stats(conn: sqlite3.Connection, *, league_id: int, season: int) -> set[int]:
    """Team ids that already have a team_season_stats row for this league/season.

    Used to resume a player/team-stats backfill: team_statistics is fetched
    last in that pipeline, so its presence marks a team as fully done.
    """
    cursor = conn.execute(
        "SELECT team_id FROM team_season_stats WHERE league_id = ? AND season = ?",
        (league_id, season),
    )
    return {row[0] for row in cursor.fetchall()}


def _parse_measurement(raw: Any) -> int | None:
    """Parse a height/weight field that may be '182', '182 cm', '79 kg', or empty/None."""
    if not raw:
        return None
    digits = re.sub(r"[^\d]", "", str(raw))
    return int(digits) if digits else None


def upsert_player(conn: sqlite3.Connection, *, player: dict[str, Any]) -> None:
    birth = player.get("birth") or {}
    conn.execute(
        """
        INSERT INTO players (id, name, firstname, lastname, birth_date, birth_country, nationality, height_cm, weight_kg)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name, firstname = excluded.firstname, lastname = excluded.lastname,
            birth_date = excluded.birth_date, birth_country = excluded.birth_country,
            nationality = excluded.nationality, height_cm = excluded.height_cm, weight_kg = excluded.weight_kg
        """,
        (
            player.get("id"),
            player.get("name", ""),
            player.get("firstname"),
            player.get("lastname"),
            birth.get("date"),
            birth.get("country"),
            player.get("nationality"),
            _parse_measurement(player.get("height")),
            _parse_measurement(player.get("weight")),
        ),
    )


def store_players_payload(conn: sqlite3.Connection, payload: dict[str, Any], *, league_id: int, season: int) -> int:
    """Normalize and store one page of the /players response. Returns player-rows stored."""
    stored = 0
    for item in payload.get("response", []):
        player = item.get("player", {})
        if player.get("id") is None:
            continue
        upsert_player(conn, player=player)
        for stats in item.get("statistics", []):
            team = stats.get("team") or {}
            team_id = team.get("id")
            if team_id is None:
                continue
            games = stats.get("games") or {}
            substitutes = stats.get("substitutes") or {}
            shots = stats.get("shots") or {}
            goals = stats.get("goals") or {}
            passes = stats.get("passes") or {}
            tackles = stats.get("tackles") or {}
            duels = stats.get("duels") or {}
            dribbles = stats.get("dribbles") or {}
            fouls = stats.get("fouls") or {}
            cards = stats.get("cards") or {}
            penalty = stats.get("penalty") or {}
            conn.execute(
                """
                INSERT INTO player_season_stats (
                    player_id, team_id, league_id, season, position, appearances, lineups, minutes, rating,
                    shots_total, shots_on, goals, assists, saves, passes_total, passes_key,
                    tackles_total, blocks, interceptions, duels_total, duels_won,
                    dribbles_attempts, dribbles_success, fouls_drawn, fouls_committed,
                    cards_yellow, cards_red, penalty_scored, penalty_missed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(player_id, team_id, league_id, season) DO UPDATE SET
                    position = excluded.position, appearances = excluded.appearances, lineups = excluded.lineups,
                    minutes = excluded.minutes, rating = excluded.rating,
                    shots_total = excluded.shots_total, shots_on = excluded.shots_on,
                    goals = excluded.goals, assists = excluded.assists, saves = excluded.saves,
                    passes_total = excluded.passes_total, passes_key = excluded.passes_key,
                    tackles_total = excluded.tackles_total, blocks = excluded.blocks, interceptions = excluded.interceptions,
                    duels_total = excluded.duels_total, duels_won = excluded.duels_won,
                    dribbles_attempts = excluded.dribbles_attempts, dribbles_success = excluded.dribbles_success,
                    fouls_drawn = excluded.fouls_drawn, fouls_committed = excluded.fouls_committed,
                    cards_yellow = excluded.cards_yellow, cards_red = excluded.cards_red,
                    penalty_scored = excluded.penalty_scored, penalty_missed = excluded.penalty_missed
                """,
                (
                    player["id"], team_id, league_id, season,
                    games.get("position"), games.get("appearences"), games.get("lineups"), games.get("minutes"),
                    _clean_stat_value(games.get("rating")),
                    shots.get("total"), shots.get("on"),
                    goals.get("total"), goals.get("assists"), goals.get("saves"),
                    passes.get("total"), passes.get("key"),
                    tackles.get("total"), tackles.get("blocks"), tackles.get("interceptions"),
                    duels.get("total"), duels.get("won"),
                    dribbles.get("attempts"), dribbles.get("success"),
                    fouls.get("drawn"), fouls.get("committed"),
                    cards.get("yellow"), cards.get("red"),
                    penalty.get("scored"), penalty.get("missed"),
                ),
            )
            stored += 1
            _ = substitutes  # not stored yet - bench/in/out are low priority, kept for future use
    conn.commit()
    return stored


def store_team_statistics_payload(conn: sqlite3.Connection, payload: dict[str, Any], *, league_id: int, season: int, team_id: int) -> bool:
    """Normalize and store the /teams/statistics response. Returns True if stored."""
    data = payload.get("response")
    if not data:
        return False
    fixtures_ = data.get("fixtures", {})
    wins = fixtures_.get("wins", {})
    draws = fixtures_.get("draws", {})
    loses = fixtures_.get("loses", {})
    goals = data.get("goals", {})
    goals_for = goals.get("for", {})
    goals_against = goals.get("against", {})
    clean_sheet = data.get("clean_sheet", {})
    failed_to_score = data.get("failed_to_score", {})
    penalty = data.get("penalty", {})
    biggest = data.get("biggest", {})
    biggest_streak = biggest.get("streak", {})

    conn.execute(
        """
        INSERT INTO team_season_stats (
            league_id, season, team_id, form, played_total,
            wins_home, wins_away, wins_total, draws_home, draws_away, draws_total,
            loses_home, loses_away, loses_total,
            goals_for_home, goals_for_away, goals_for_total,
            goals_against_home, goals_against_away, goals_against_total,
            clean_sheets_home, clean_sheets_away, clean_sheets_total,
            failed_to_score_home, failed_to_score_away, failed_to_score_total,
            penalty_scored_total, penalty_missed_total,
            biggest_win_streak, biggest_loss_streak,
            goals_for_minutes_json, goals_for_under_over_json,
            goals_against_minutes_json, goals_against_under_over_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(league_id, season, team_id) DO UPDATE SET
            form = excluded.form, played_total = excluded.played_total,
            wins_home = excluded.wins_home, wins_away = excluded.wins_away, wins_total = excluded.wins_total,
            draws_home = excluded.draws_home, draws_away = excluded.draws_away, draws_total = excluded.draws_total,
            loses_home = excluded.loses_home, loses_away = excluded.loses_away, loses_total = excluded.loses_total,
            goals_for_home = excluded.goals_for_home, goals_for_away = excluded.goals_for_away, goals_for_total = excluded.goals_for_total,
            goals_against_home = excluded.goals_against_home, goals_against_away = excluded.goals_against_away, goals_against_total = excluded.goals_against_total,
            clean_sheets_home = excluded.clean_sheets_home, clean_sheets_away = excluded.clean_sheets_away, clean_sheets_total = excluded.clean_sheets_total,
            failed_to_score_home = excluded.failed_to_score_home, failed_to_score_away = excluded.failed_to_score_away, failed_to_score_total = excluded.failed_to_score_total,
            penalty_scored_total = excluded.penalty_scored_total, penalty_missed_total = excluded.penalty_missed_total,
            biggest_win_streak = excluded.biggest_win_streak, biggest_loss_streak = excluded.biggest_loss_streak,
            goals_for_minutes_json = excluded.goals_for_minutes_json, goals_for_under_over_json = excluded.goals_for_under_over_json,
            goals_against_minutes_json = excluded.goals_against_minutes_json, goals_against_under_over_json = excluded.goals_against_under_over_json
        """,
        (
            league_id, season, team_id, data.get("form"), fixtures_.get("played", {}).get("total"),
            wins.get("home"), wins.get("away"), wins.get("total"),
            draws.get("home"), draws.get("away"), draws.get("total"),
            loses.get("home"), loses.get("away"), loses.get("total"),
            (goals_for.get("total") or {}).get("home"), (goals_for.get("total") or {}).get("away"), (goals_for.get("total") or {}).get("total"),
            (goals_against.get("total") or {}).get("home"), (goals_against.get("total") or {}).get("away"), (goals_against.get("total") or {}).get("total"),
            clean_sheet.get("home"), clean_sheet.get("away"), clean_sheet.get("total"),
            failed_to_score.get("home"), failed_to_score.get("away"), failed_to_score.get("total"),
            (penalty.get("scored") or {}).get("total"), (penalty.get("missed") or {}).get("total"),
            biggest_streak.get("wins"), biggest_streak.get("loses"),
            json.dumps(goals_for.get("minute")) if goals_for.get("minute") else None,
            json.dumps(goals_for.get("under_over")) if goals_for.get("under_over") else None,
            json.dumps(goals_against.get("minute")) if goals_against.get("minute") else None,
            json.dumps(goals_against.get("under_over")) if goals_against.get("under_over") else None,
        ),
    )
    conn.commit()
    return True


def store_standings_snapshot(
    conn: sqlite3.Connection,
    *,
    league_id: int,
    season: int,
    matchday: int,
    team_id: int,
    played: int,
    won: int,
    drawn: int,
    lost: int,
    goals_for: int,
    goals_against: int,
    points: int,
    position: int,
) -> None:
    conn.execute(
        """
        INSERT INTO standings_snapshots (
            league_id, season, matchday, team_id, played, won, drawn, lost,
            goals_for, goals_against, goal_diff, points, position
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(league_id, season, matchday, team_id) DO UPDATE SET
            played = excluded.played, won = excluded.won, drawn = excluded.drawn, lost = excluded.lost,
            goals_for = excluded.goals_for, goals_against = excluded.goals_against,
            goal_diff = excluded.goal_diff, points = excluded.points, position = excluded.position
        """,
        (
            league_id,
            season,
            matchday,
            team_id,
            played,
            won,
            drawn,
            lost,
            goals_for,
            goals_against,
            goals_for - goals_against,
            points,
            position,
        ),
    )


def store_fixture_importance(
    conn: sqlite3.Connection,
    *,
    fixture_id: int,
    importance: float,
    is_derby: bool,
    title_component: float,
    europe_component: float,
    relegation_component: float,
    season_weight: float,
    home_position_before: int | None,
    away_position_before: int | None,
) -> None:
    conn.execute(
        """
        INSERT INTO fixture_importance (
            fixture_id, importance, is_derby, title_component, europe_component,
            relegation_component, season_weight, home_position_before, away_position_before
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fixture_id) DO UPDATE SET
            importance = excluded.importance,
            is_derby = excluded.is_derby,
            title_component = excluded.title_component,
            europe_component = excluded.europe_component,
            relegation_component = excluded.relegation_component,
            season_weight = excluded.season_weight,
            home_position_before = excluded.home_position_before,
            away_position_before = excluded.away_position_before
        """,
        (
            fixture_id,
            importance,
            int(is_derby),
            title_component,
            europe_component,
            relegation_component,
            season_weight,
            home_position_before,
            away_position_before,
        ),
    )
