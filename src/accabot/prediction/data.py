from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Match:
    fixture_id: int
    season: int
    matchday: int | None
    kickoff: datetime
    home_id: int
    away_id: int
    home_goals: int
    away_goals: int


def load_matches(
    conn: sqlite3.Connection,
    *,
    league_id: int = 39,
    seasons: tuple[int, int] | None = None,
) -> list[Match]:
    """Load completed fixtures for a league, ordered by kickoff time.

    ``seasons`` is an inclusive ``(start, end)`` range of season-start years.
    Only fixtures with both goal counts recorded are returned.
    """
    query = (
        "SELECT id, season, matchday, kickoff_utc, home_team_id, away_team_id, home_goals, away_goals "
        "FROM fixtures WHERE league_id = ? AND home_goals IS NOT NULL AND away_goals IS NOT NULL"
    )
    params: list[object] = [league_id]
    if seasons is not None:
        query += " AND season BETWEEN ? AND ?"
        params.extend(seasons)
    query += " ORDER BY kickoff_utc"

    rows = conn.execute(query, params).fetchall()
    matches = []
    for fixture_id, season, matchday, kickoff_utc, home_id, away_id, home_goals, away_goals in rows:
        matches.append(
            Match(
                fixture_id=fixture_id,
                season=season,
                matchday=matchday,
                kickoff=datetime.fromisoformat(kickoff_utc),
                home_id=home_id,
                away_id=away_id,
                home_goals=home_goals,
                away_goals=away_goals,
            )
        )
    return matches


def team_names(conn: sqlite3.Connection) -> dict[int, str]:
    rows = conn.execute("SELECT id, name FROM teams").fetchall()
    return {team_id: name for team_id, name in rows}
