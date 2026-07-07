import unittest

from accabot.importance import (
    MatchResult,
    TeamState,
    _cutoff_component,
    _title_component,
    compute_importance,
    is_derby,
    rank_teams,
)


class RankTeamsTest(unittest.TestCase):
    def test_orders_by_points_then_goal_diff_then_goals_for(self) -> None:
        table = {
            1: TeamState(won=2, drawn=1, lost=0, goals_for=5, goals_against=2),  # 7 pts, gd 3
            2: TeamState(won=2, drawn=1, lost=0, goals_for=6, goals_against=3),  # 7 pts, gd 3, more gf
            3: TeamState(won=1, drawn=0, lost=2, goals_for=2, goals_against=5),  # 3 pts
        }
        self.assertEqual(rank_teams(table), [2, 1, 3])


class ComponentFormulaTest(unittest.TestCase):
    def test_title_component_is_max_for_leader(self) -> None:
        self.assertEqual(_title_component(points=50, leader_points=50, games_remaining=5), 1.0)

    def test_title_component_drops_when_mathematically_out(self) -> None:
        # 1 game remaining => max 3 points swing; 10 point gap is unreachable.
        self.assertEqual(_title_component(points=40, leader_points=50, games_remaining=1), 0.0)

    def test_cutoff_component_peaks_on_the_line(self) -> None:
        self.assertEqual(_cutoff_component(points=40, boundary_points=40, games_remaining=3), 1.0)

    def test_cutoff_component_falls_off_with_distance(self) -> None:
        close = _cutoff_component(points=38, boundary_points=40, games_remaining=10)
        far = _cutoff_component(points=10, boundary_points=40, games_remaining=10)
        self.assertGreater(close, far)


class DerbyTest(unittest.TestCase):
    def test_known_pair_is_derby(self) -> None:
        self.assertTrue(is_derby("Arsenal", "Tottenham"))
        self.assertTrue(is_derby("Tottenham", "Arsenal"))

    def test_unrelated_pair_is_not_derby(self) -> None:
        self.assertFalse(is_derby("Arsenal", "Burnley"))


class ComputeImportanceTest(unittest.TestCase):
    """Six-team synthetic league so a title/mid-table/relegation spread fits in a few matchdays."""

    TEAM_NAMES = {
        1: "Leaders FC",
        2: "Chasers FC",
        3: "Mid A",
        4: "Mid B",
        5: "Strugglers A",
        6: "Strugglers B",
    }

    def test_tied_leaders_meeting_scores_max_title_component(self) -> None:
        # Teams 1 and 2 are joint top on points and play each other in matchday 3.
        results = [
            MatchResult(fixture_id=100, matchday=1, home_team_id=1, away_team_id=3, home_goals=2, away_goals=0),
            MatchResult(fixture_id=101, matchday=1, home_team_id=2, away_team_id=4, home_goals=2, away_goals=0),
            MatchResult(fixture_id=102, matchday=1, home_team_id=5, away_team_id=6, home_goals=1, away_goals=1),
            MatchResult(fixture_id=200, matchday=2, home_team_id=1, away_team_id=4, home_goals=1, away_goals=0),
            MatchResult(fixture_id=201, matchday=2, home_team_id=2, away_team_id=3, home_goals=1, away_goals=0),
            MatchResult(fixture_id=202, matchday=2, home_team_id=5, away_team_id=6, home_goals=0, away_goals=0),
            MatchResult(fixture_id=300, matchday=3, home_team_id=1, away_team_id=2, home_goals=0, away_goals=0),
        ]
        importance_results, _ = compute_importance(results, self.TEAM_NAMES, total_teams=6)
        title_decider = next(item for item in importance_results if item.fixture_id == 300)

        self.assertEqual(title_decider.title_component, 1.0)

    def test_relegation_six_pointer_scores_high_relegation_component(self) -> None:
        # Small 4-team league so the schedule is easy to hand-construct: total_matchdays == 6
        # matches the double round-robin below exactly, avoiding games-remaining artifacts.
        # Teams 3 and 4 stay bottom throughout and meet on the final matchday, level on points,
        # right on a relegation boundary set at rank 3/4 (bottom team goes down).
        results = [
            MatchResult(fixture_id=1, matchday=1, home_team_id=1, away_team_id=4, home_goals=2, away_goals=0),
            MatchResult(fixture_id=2, matchday=1, home_team_id=2, away_team_id=3, home_goals=2, away_goals=0),
            MatchResult(fixture_id=3, matchday=2, home_team_id=1, away_team_id=3, home_goals=2, away_goals=0),
            MatchResult(fixture_id=4, matchday=2, home_team_id=4, away_team_id=2, home_goals=0, away_goals=2),
            MatchResult(fixture_id=5, matchday=3, home_team_id=1, away_team_id=2, home_goals=1, away_goals=1),
            MatchResult(fixture_id=6, matchday=3, home_team_id=3, away_team_id=4, home_goals=1, away_goals=1),
            MatchResult(fixture_id=7, matchday=4, home_team_id=4, away_team_id=1, home_goals=0, away_goals=2),
            MatchResult(fixture_id=8, matchday=4, home_team_id=3, away_team_id=2, home_goals=0, away_goals=2),
            MatchResult(fixture_id=9, matchday=5, home_team_id=3, away_team_id=1, home_goals=0, away_goals=2),
            MatchResult(fixture_id=10, matchday=5, home_team_id=2, away_team_id=4, home_goals=2, away_goals=0),
            MatchResult(fixture_id=11, matchday=6, home_team_id=2, away_team_id=1, home_goals=1, away_goals=1),
            MatchResult(fixture_id=12, matchday=6, home_team_id=4, away_team_id=3, home_goals=1, away_goals=1),
        ]
        importance_results, _ = compute_importance(
            results,
            self.TEAM_NAMES,
            total_teams=4,
            europe_boundary_rank=1,
            relegation_boundary_rank=3,
        )
        six_pointer = next(item for item in importance_results if item.fixture_id == 12)

        self.assertGreater(six_pointer.relegation_component, 0.9)
        self.assertGreater(six_pointer.importance, 0.5)

    def test_season_weight_increases_across_matchdays(self) -> None:
        results = [
            MatchResult(fixture_id=1, matchday=1, home_team_id=1, away_team_id=2, home_goals=1, away_goals=0),
            MatchResult(fixture_id=2, matchday=1, home_team_id=3, away_team_id=4, home_goals=1, away_goals=0),
            MatchResult(fixture_id=3, matchday=1, home_team_id=5, away_team_id=6, home_goals=1, away_goals=0),
            MatchResult(fixture_id=4, matchday=2, home_team_id=1, away_team_id=3, home_goals=1, away_goals=0),
            MatchResult(fixture_id=5, matchday=2, home_team_id=2, away_team_id=4, home_goals=1, away_goals=0),
            MatchResult(fixture_id=6, matchday=2, home_team_id=5, away_team_id=6, home_goals=1, away_goals=0),
        ]
        importance_results, _ = compute_importance(results, self.TEAM_NAMES, total_teams=6)
        by_matchday_weight = {item.fixture_id: item.season_weight for item in importance_results}
        self.assertLess(by_matchday_weight[1], by_matchday_weight[4])

    def test_snapshots_reflect_state_before_matchday_not_after(self) -> None:
        results = [
            MatchResult(fixture_id=1, matchday=1, home_team_id=1, away_team_id=2, home_goals=3, away_goals=0),
        ]
        _, snapshots = compute_importance(results, self.TEAM_NAMES, total_teams=6)
        matchday_one_snapshot = {row["team_id"]: row for row in snapshots if row["matchday"] == 1}
        # Snapshot tags "matchday 1" as the state entering matchday 1, i.e. before it was played.
        self.assertEqual(matchday_one_snapshot[1]["played"], 0)
        self.assertEqual(matchday_one_snapshot[1]["points"], 0)


if __name__ == "__main__":
    unittest.main()
