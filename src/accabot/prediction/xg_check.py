from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import timezone

import numpy as np

from .data import load_matches
from .poisson import DixonColesModel


@dataclass
class ExpectedGoalsComparison:
    season: int
    fixture_id: int
    home_goals: int
    away_goals: int
    home_xg: float
    away_xg: float
    model_lambda_home: float
    model_mu_away: float


def _load_fixture_xg(conn: sqlite3.Connection, *, league_id: int) -> dict[int, tuple[float, float]]:
    rows = conn.execute(
        """
        SELECT f.id, fs.team_id, f.home_team_id, fs.expected_goals
        FROM fixtures f
        JOIN fixture_stats fs ON fs.fixture_id = f.id
        WHERE f.league_id = ? AND fs.expected_goals IS NOT NULL
        """,
        (league_id,),
    ).fetchall()
    per_fixture: dict[int, dict[str, float]] = defaultdict(dict)
    for fixture_id, team_id, home_team_id, xg in rows:
        side = "home" if team_id == home_team_id else "away"
        per_fixture[fixture_id][side] = xg
    return {fid: (sides["home"], sides["away"]) for fid, sides in per_fixture.items() if "home" in sides and "away" in sides}


def compare_predicted_goals_to_xg(
    conn: sqlite3.Connection,
    *,
    league_id: int = 39,
    burn_in_end_season: int = 2021,
    xg_end_season: int = 2025,
    xi: float = 0.0018,
) -> list[ExpectedGoalsComparison]:
    """Walk-forward predict each team's expected goals (lambda/mu) for every
    fixture that has recorded post-match xG, using only data strictly before
    that fixture, and pair the two up for comparison.

    lambda/mu are a *prior* expectation from team strength going into the
    match; xG is a *post-hoc* measure of the chances actually created in that
    single match. They measure different things and won't line up 1:1, but a
    model whose priors are realistic should still correlate with them.
    """
    xg_by_fixture = _load_fixture_xg(conn, league_id=league_id)
    if not xg_by_fixture:
        raise ValueError("No fixtures with recorded expected_goals found.")

    all_matches = load_matches(conn, league_id=league_id, seasons=(2010, xg_end_season))
    team_ids = sorted({t for m in all_matches for t in (m.home_id, m.away_id)})

    by_matchday: dict[tuple[int, int], list] = defaultdict(list)
    for m in all_matches:
        by_matchday[(m.season, m.matchday)].append(m)
    ordered_keys = sorted(by_matchday.keys())

    training = [m for m in all_matches if m.season <= burn_in_end_season]
    validation_keys = [k for k in ordered_keys if k[0] > burn_in_end_season]

    results: list[ExpectedGoalsComparison] = []
    x_prev = None
    for key in validation_keys:
        group = by_matchday[key]
        as_of = min(m.kickoff for m in group)
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)

        model = DixonColesModel(xi=xi)
        model.fit(training, team_ids=team_ids, as_of=as_of, x0=x_prev)
        x_prev = model._last_x

        for match in group:
            xg = xg_by_fixture.get(match.fixture_id)
            if xg is not None:
                lam, mu = model.goal_expectations(match.home_id, match.away_id)
                home_xg, away_xg = xg
                results.append(
                    ExpectedGoalsComparison(
                        season=match.season,
                        fixture_id=match.fixture_id,
                        home_goals=match.home_goals,
                        away_goals=match.away_goals,
                        home_xg=home_xg,
                        away_xg=away_xg,
                        model_lambda_home=lam,
                        model_mu_away=mu,
                    )
                )

        training = training + group

    return results


def summarize(results: list[ExpectedGoalsComparison], *, real_only_through_season: int | None = None) -> None:
    def _report(rows: list[ExpectedGoalsComparison], label: str) -> None:
        if not rows:
            print(f"{label}: no fixtures.")
            return
        model_g = np.array([r.model_lambda_home for r in rows] + [r.model_mu_away for r in rows])
        xg = np.array([r.home_xg for r in rows] + [r.away_xg for r in rows])
        actual_goals = np.array([r.home_goals for r in rows] + [r.away_goals for r in rows], dtype=float)

        corr_model_xg = np.corrcoef(model_g, xg)[0, 1]
        corr_model_goals = np.corrcoef(model_g, actual_goals)[0, 1]
        corr_xg_goals = np.corrcoef(xg, actual_goals)[0, 1]
        mae_model_xg = np.mean(np.abs(model_g - xg))

        print(f"\n=== {label} (n={len(rows)} fixtures, {2 * len(rows)} team-match rows) ===")
        print(f"corr(model expected goals, match xG):      {corr_model_xg:.3f}")
        print(f"corr(model expected goals, actual goals):   {corr_model_goals:.3f}")
        print(f"corr(match xG, actual goals):                {corr_xg_goals:.3f}   (xG's own ceiling vs reality)")
        print(f"MAE(model expected goals vs match xG):      {mae_model_xg:.3f}")
        print(f"mean model expected goals: {model_g.mean():.3f}  mean xG: {xg.mean():.3f}  mean actual goals: {actual_goals.mean():.3f}")

    if real_only_through_season is not None:
        real = [r for r in results if r.season <= real_only_through_season]
        projected = [r for r in results if r.season > real_only_through_season]
        _report(real, "real seasons")
        if projected:
            _report(projected, "projected/simulated season(s) - dry-run only, not a validation signal")
    else:
        _report(results, "all seasons")
