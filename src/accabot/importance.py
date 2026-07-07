from __future__ import annotations

from dataclasses import dataclass, replace

# Manually curated; extend as needed. Matched against API-Football's team `name` field.
_DERBY_NAME_PAIRS: tuple[tuple[str, str], ...] = (
    ("Manchester United", "Manchester City"),
    ("Manchester United", "Liverpool"),
    ("Liverpool", "Everton"),
    ("Arsenal", "Tottenham"),
    ("Arsenal", "Chelsea"),
    ("Chelsea", "Tottenham"),
    ("Newcastle", "Sunderland"),
    ("Aston Villa", "Birmingham"),
    ("West Ham", "Millwall"),
    ("Crystal Palace", "Brighton"),
)
DERBY_PAIRS: frozenset[frozenset[str]] = frozenset(frozenset(pair) for pair in _DERBY_NAME_PAIRS)


def is_derby(home_name: str, away_name: str) -> bool:
    return frozenset({home_name, away_name}) in DERBY_PAIRS


@dataclass(frozen=True)
class MatchResult:
    fixture_id: int
    matchday: int
    home_team_id: int
    away_team_id: int
    home_goals: int
    away_goals: int


@dataclass(frozen=True)
class TeamState:
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    goals_for: int = 0
    goals_against: int = 0

    @property
    def points(self) -> int:
        return self.won * 3 + self.drawn

    @property
    def goal_diff(self) -> int:
        return self.goals_for - self.goals_against

    def apply_result(self, goals_for: int, goals_against: int) -> "TeamState":
        return replace(
            self,
            played=self.played + 1,
            won=self.won + (1 if goals_for > goals_against else 0),
            drawn=self.drawn + (1 if goals_for == goals_against else 0),
            lost=self.lost + (1 if goals_for < goals_against else 0),
            goals_for=self.goals_for + goals_for,
            goals_against=self.goals_against + goals_against,
        )


@dataclass(frozen=True)
class ImportanceResult:
    fixture_id: int
    importance: float
    is_derby: bool
    title_component: float
    europe_component: float
    relegation_component: float
    season_weight: float
    home_position_before: int
    away_position_before: int


def rank_teams(table: dict[int, TeamState]) -> list[int]:
    """Team ids ordered best-first: points, then goal diff, then goals for.

    Ties fall back to team id purely for determinism - this is a simplification
    of the official head-to-head tiebreaker, which isn't worth the complexity
    for a stakes heuristic.
    """
    return sorted(
        table.keys(),
        key=lambda tid: (-table[tid].points, -table[tid].goal_diff, -table[tid].goals_for, tid),
    )


def _points_at_rank(ranking: list[int], table: dict[int, TeamState], rank: int) -> float:
    index = max(0, min(rank - 1, len(ranking) - 1))
    return float(table[ranking[index]].points)


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _title_component(points: int, leader_points: int, games_remaining: int) -> float:
    max_swing = max(games_remaining * 3, 1)
    gap = max(0, leader_points - points)
    return _clip01(1 - gap / max_swing)


def _cutoff_component(points: int, boundary_points: float, games_remaining: int) -> float:
    """How close a team sits to a table boundary line (e.g. 4th/5th, 17th/18th).

    Symmetric: a team just above or just below the line both score highly,
    since the fixture matters whether they're defending the spot or chasing it.
    """
    max_swing = max(games_remaining * 3, 1)
    gap = abs(points - boundary_points)
    return _clip01(1 - gap / max_swing)


def compute_importance(
    results: list[MatchResult],
    team_names: dict[int, str],
    *,
    total_teams: int | None = None,
    europe_boundary_rank: int = 4,
    relegation_boundary_rank: int | None = None,
    derby_bonus: float = 0.05,
) -> tuple[list[ImportanceResult], list[dict]]:
    """Score each fixture's stakes using only results from strictly earlier matchdays.

    Returns (per-fixture importance results, standings snapshot rows). Each snapshot
    row reflects the table as it stood BEFORE the matchday it's tagged with, which is
    also the state the corresponding importance scores were computed from - training
    on this avoids leaking future-season outcomes into a "how much was riding on this
    match" feature.
    """
    all_team_ids = sorted({r.home_team_id for r in results} | {r.away_team_id for r in results})
    if total_teams is None:
        total_teams = len(all_team_ids)
    total_matchdays = max((total_teams - 1) * 2, 1)
    if relegation_boundary_rank is None:
        relegation_boundary_rank = max(total_teams - 3, 1)

    table: dict[int, TeamState] = {tid: TeamState() for tid in all_team_ids}

    by_matchday: dict[int, list[MatchResult]] = {}
    for result in results:
        by_matchday.setdefault(result.matchday, []).append(result)

    importance_results: list[ImportanceResult] = []
    snapshots: list[dict] = []

    for matchday in sorted(by_matchday):
        ranking = rank_teams(table)
        positions = {tid: index + 1 for index, tid in enumerate(ranking)}
        leader_points = _points_at_rank(ranking, table, 1)
        europe_boundary = (
            _points_at_rank(ranking, table, europe_boundary_rank)
            + _points_at_rank(ranking, table, europe_boundary_rank + 1)
        ) / 2
        relegation_boundary = (
            _points_at_rank(ranking, table, relegation_boundary_rank)
            + _points_at_rank(ranking, table, relegation_boundary_rank + 1)
        ) / 2

        for team_id in all_team_ids:
            state = table[team_id]
            snapshots.append(
                {
                    "matchday": matchday,
                    "team_id": team_id,
                    "played": state.played,
                    "won": state.won,
                    "drawn": state.drawn,
                    "lost": state.lost,
                    "goals_for": state.goals_for,
                    "goals_against": state.goals_against,
                    "points": state.points,
                    "position": positions[team_id],
                }
            )

        season_fraction = (matchday - 1) / total_matchdays
        season_weight = 0.4 + 0.6 * min(season_fraction, 1.0)

        for match in by_matchday[matchday]:
            home_games_remaining = total_matchdays - table[match.home_team_id].played
            away_games_remaining = total_matchdays - table[match.away_team_id].played

            title_component = (
                _title_component(table[match.home_team_id].points, leader_points, home_games_remaining)
                + _title_component(table[match.away_team_id].points, leader_points, away_games_remaining)
            ) / 2
            europe_component = (
                _cutoff_component(table[match.home_team_id].points, europe_boundary, home_games_remaining)
                + _cutoff_component(table[match.away_team_id].points, europe_boundary, away_games_remaining)
            ) / 2
            relegation_component = (
                _cutoff_component(table[match.home_team_id].points, relegation_boundary, home_games_remaining)
                + _cutoff_component(table[match.away_team_id].points, relegation_boundary, away_games_remaining)
            ) / 2

            stakes = max(title_component, europe_component, relegation_component)
            derby = is_derby(
                team_names.get(match.home_team_id, ""),
                team_names.get(match.away_team_id, ""),
            )
            importance = _clip01(stakes * season_weight + (derby_bonus if derby else 0.0))

            importance_results.append(
                ImportanceResult(
                    fixture_id=match.fixture_id,
                    importance=importance,
                    is_derby=derby,
                    title_component=title_component,
                    europe_component=europe_component,
                    relegation_component=relegation_component,
                    season_weight=season_weight,
                    home_position_before=positions[match.home_team_id],
                    away_position_before=positions[match.away_team_id],
                )
            )

        for match in by_matchday[matchday]:
            table[match.home_team_id] = table[match.home_team_id].apply_result(match.home_goals, match.away_goals)
            table[match.away_team_id] = table[match.away_team_id].apply_result(match.away_goals, match.home_goals)

    return importance_results, snapshots
