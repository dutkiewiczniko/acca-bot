from __future__ import annotations

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
