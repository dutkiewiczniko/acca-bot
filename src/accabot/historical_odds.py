from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .prediction.teams import build_team_name_index, resolve_team_id
from .storage import upsert_historical_odds

BASE_URL = "https://www.football-data.co.uk/mmz4281"

# Season start year -> football-data.co.uk season code (two two-digit years).
PREMIER_LEAGUE_SEASON_CODES = {year: f"{str(year)[-2:]}{str(year + 1)[-2:]}" for year in range(1993, 2031)}


@dataclass
class OddsBackfillReport:
    season: int
    rows_in_csv: int = 0
    matched: int = 0
    stored: int = 0
    unmatched_pairs: list[tuple[str, str]] = None  # type: ignore[assignment]
    error: str | None = None

    def __post_init__(self) -> None:
        if self.unmatched_pairs is None:
            self.unmatched_pairs = []


def _float(row: dict[str, str], *candidates: str) -> float | None:
    """Return the first present, parseable, positive decimal odd among candidate columns."""
    for name in candidates:
        raw = row.get(name)
        if raw is None:
            continue
        raw = raw.strip()
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        if value > 1.0:
            return value
    return None


def fetch_season_csv(season: int, *, timeout: int = 30) -> str:
    code = PREMIER_LEAGUE_SEASON_CODES.get(season)
    if code is None:
        raise ValueError(f"No football-data.co.uk season code known for {season}.")
    url = f"{BASE_URL}/{code}/E0.csv"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (acca-bot historical odds backfill)"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} fetching {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach {url}: {exc.reason}") from exc


def _fixture_lookup(conn: sqlite3.Connection, *, league_id: int, season: int) -> dict[tuple[int, int], int]:
    """(home_team_id, away_team_id) -> fixture_id for one league-season.

    A home/away ordering occurs at most once per season in a double round-robin,
    so this pair uniquely identifies the fixture.
    """
    rows = conn.execute(
        "SELECT id, home_team_id, away_team_id FROM fixtures WHERE league_id = ? AND season = ?",
        (league_id, season),
    ).fetchall()
    return {(home_id, away_id): fixture_id for fixture_id, home_id, away_id in rows}


def backfill_season_odds(
    conn: sqlite3.Connection,
    *,
    league_id: int = 39,
    season: int,
) -> OddsBackfillReport:
    """Download one season of football-data.co.uk E0 odds and store them against
    matching fixtures already in the database. Fixtures are matched on
    (season, home_team_id, away_team_id); rows that don't match an existing
    fixture (e.g. a name we can't resolve) are reported, not stored.
    """
    report = OddsBackfillReport(season=season)
    try:
        raw = fetch_season_csv(season)
    except (RuntimeError, ValueError) as exc:
        report.error = str(exc)
        return report

    name_index = build_team_name_index(conn)
    fixtures = _fixture_lookup(conn, league_id=league_id, season=season)

    reader = csv.DictReader(io.StringIO(raw))
    for row in reader:
        home_name = (row.get("HomeTeam") or "").strip()
        away_name = (row.get("AwayTeam") or "").strip()
        if not home_name or not away_name:
            continue
        report.rows_in_csv += 1

        home_id = resolve_team_id(home_name, name_index)
        away_id = resolve_team_id(away_name, name_index)
        fixture_id = fixtures.get((home_id, away_id)) if home_id and away_id else None
        if fixture_id is None:
            report.unmatched_pairs.append((home_name, away_name))
            continue
        report.matched += 1

        upsert_historical_odds(
            conn,
            fixture_id=fixture_id,
            source="football-data.co.uk",
            pinnacle_home=_float(row, "PSH", "PH"),
            pinnacle_draw=_float(row, "PSD", "PD"),
            pinnacle_away=_float(row, "PSA", "PA"),
            avg_home=_float(row, "AvgH", "BbAvH"),
            avg_draw=_float(row, "AvgD", "BbAvD"),
            avg_away=_float(row, "AvgA", "BbAvA"),
            pinnacle_close_home=_float(row, "PSCH"),
            pinnacle_close_draw=_float(row, "PSCD"),
            pinnacle_close_away=_float(row, "PSCA"),
            pinnacle_over25=_float(row, "P>2.5"),
            pinnacle_under25=_float(row, "P<2.5"),
            avg_over25=_float(row, "Avg>2.5", "BbAv>2.5"),
            avg_under25=_float(row, "Avg<2.5", "BbAv<2.5"),
        )
        report.stored += 1

    conn.commit()
    return report


def backfill_all_seasons(
    conn: sqlite3.Connection,
    *,
    league_id: int = 39,
    start_season: int = 2010,
    end_season: int = 2025,
) -> list[OddsBackfillReport]:
    reports = []
    for season in range(start_season, end_season + 1):
        reports.append(backfill_season_odds(conn, league_id=league_id, season=season))
    return reports
