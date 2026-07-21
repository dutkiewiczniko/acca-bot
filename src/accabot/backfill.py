from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from . import storage
from .api_football import ApiFootballClient
from .http import RateLimitedError
from .importance import MatchResult, compute_importance
from .ratelimit import DailyBudgetExceeded

PREMIER_LEAGUE_ID = 39


@dataclass
class BackfillReport:
    fixtures_stored: int = 0
    stats_fetched: int = 0
    stats_remaining: int = 0
    importance_scored: int = 0
    daily_budget_hit: bool = False
    rate_limited: bool = False
    error: str | None = None
    players_stored: int = 0
    teams_stats_stored: int = 0
    teams_pending: list[int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.teams_pending is None:
            self.teams_pending = []


def backfill_season(
    client: ApiFootballClient,
    conn: sqlite3.Connection,
    *,
    league_id: int = PREMIER_LEAGUE_ID,
    season: int,
    fetch_stats: bool = True,
) -> BackfillReport:
    report = BackfillReport()

    try:
        payload = client.fixtures(league=league_id, season=season)
    except DailyBudgetExceeded:
        report.daily_budget_hit = True
        return report
    except RateLimitedError as exc:
        report.rate_limited = True
        report.error = str(exc)
        return report

    errors = payload.get("errors")
    if errors:
        report.error = str(errors)
        return report

    report.fixtures_stored = storage.store_fixtures_payload(conn, payload, league_id=league_id, season=season)

    if fetch_stats:
        pending = storage.fetch_fixtures_missing_stats(conn, league_id=league_id, season=season)
        for fixture_row in pending:
            try:
                stats_payload = client.fixture_statistics(fixture=fixture_row["id"])
            except DailyBudgetExceeded:
                report.daily_budget_hit = True
                break
            except RateLimitedError:
                report.rate_limited = True
                break
            storage.store_fixture_statistics_payload(conn, stats_payload, fixture_id=fixture_row["id"])
            report.stats_fetched += 1
        report.stats_remaining = len(storage.fetch_fixtures_missing_stats(conn, league_id=league_id, season=season))

    report.importance_scored = compute_and_store_importance(conn, league_id=league_id, season=season)
    return report


def backfill_player_and_team_stats(
    client: ApiFootballClient,
    conn: sqlite3.Connection,
    *,
    league_id: int = PREMIER_LEAGUE_ID,
    season: int,
) -> BackfillReport:
    """Pull per-player season stats and per-team season aggregates for one season.

    Requires the season's fixtures to already be backfilled (team ids are read
    from stored fixtures, not fetched fresh - no fixtures API call spent here).
    Resumable: teams already stored for this season/league are skipped on a
    re-run, so hitting the daily budget partway through just means running
    the same command again once the budget refills.
    """
    report = BackfillReport()
    team_ids = storage.fetch_teams_for_season(conn, league_id=league_id, season=season)
    already_done = storage.fetch_teams_with_season_stats(conn, league_id=league_id, season=season)
    pending = [team_id for team_id in team_ids if team_id not in already_done]

    for team_id in pending:
        try:
            page = 1
            while True:
                payload = client.players(league=league_id, season=season, team=team_id, page=page)
                errors = payload.get("errors")
                if errors:
                    report.error = str(errors)
                    break
                report.players_stored += storage.store_players_payload(conn, payload, league_id=league_id, season=season)
                paging = payload.get("paging") or {}
                if page >= (paging.get("total") or 1):
                    break
                page += 1

            stats_payload = client.team_statistics(league=league_id, season=season, team=team_id)
            if storage.store_team_statistics_payload(conn, stats_payload, league_id=league_id, season=season, team_id=team_id):
                report.teams_stats_stored += 1
        except DailyBudgetExceeded:
            report.daily_budget_hit = True
            break
        except RateLimitedError as exc:
            report.rate_limited = True
            report.error = str(exc)
            break

    report.teams_pending = [
        team_id for team_id in team_ids if team_id not in storage.fetch_teams_with_season_stats(conn, league_id=league_id, season=season)
    ]
    return report


def compute_and_store_importance(conn: sqlite3.Connection, *, league_id: int, season: int) -> int:
    """Recompute standings snapshots and match importance from stored results.

    Pure local computation over whatever fixtures/results are already in the
    database - no API calls, so this is safe to re-run any time (e.g. after a
    fresh batch of fixtures lands from a resumed backfill).
    """
    fixture_rows = storage.fetch_season_fixtures(conn, league_id=league_id, season=season)
    team_names = storage.fetch_team_names(conn)

    results = [
        MatchResult(
            fixture_id=row["id"],
            matchday=row["matchday"],
            home_team_id=row["home_team_id"],
            away_team_id=row["away_team_id"],
            home_goals=row["home_goals"],
            away_goals=row["away_goals"],
        )
        for row in fixture_rows
        if row["status"] == "FT" and row["home_goals"] is not None and row["away_goals"] is not None
    ]
    if not results:
        return 0

    importance_results, snapshots = compute_importance(results, team_names)

    for snapshot in snapshots:
        storage.store_standings_snapshot(
            conn,
            league_id=league_id,
            season=season,
            matchday=snapshot["matchday"],
            team_id=snapshot["team_id"],
            played=snapshot["played"],
            won=snapshot["won"],
            drawn=snapshot["drawn"],
            lost=snapshot["lost"],
            goals_for=snapshot["goals_for"],
            goals_against=snapshot["goals_against"],
            points=snapshot["points"],
            position=snapshot["position"],
        )

    for item in importance_results:
        storage.store_fixture_importance(
            conn,
            fixture_id=item.fixture_id,
            importance=item.importance,
            is_derby=item.is_derby,
            title_component=item.title_component,
            europe_component=item.europe_component,
            relegation_component=item.relegation_component,
            season_weight=item.season_weight,
            home_position_before=item.home_position_before,
            away_position_before=item.away_position_before,
        )

    conn.commit()
    return len(importance_results)
