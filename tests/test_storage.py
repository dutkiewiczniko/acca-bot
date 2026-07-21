import sqlite3
import unittest

from accabot import storage

FIXTURES_PAYLOAD = {
    "response": [
        {
            "fixture": {
                "id": 1001,
                "referee": "M. Oliver",
                "date": "2024-08-17T14:00:00+00:00",
                "venue": {"name": "Emirates Stadium"},
                "status": {"short": "FT"},
            },
            "league": {"round": "Regular Season - 1", "country": "England"},
            "teams": {
                "home": {"id": 42, "name": "Arsenal"},
                "away": {"id": 49, "name": "Chelsea"},
            },
            "goals": {"home": 2, "away": 1},
            "score": {"halftime": {"home": 1, "away": 0}},
        }
    ]
}

STATS_PAYLOAD = {
    "response": [
        {
            "team": {"id": 42, "name": "Arsenal"},
            "statistics": [
                {"type": "Total Shots", "value": 10},
                {"type": "Ball Possession", "value": "55%"},
                {"type": "expected_goals", "value": "1.8"},
                {"type": "Red Cards", "value": None},
            ],
        }
    ]
}


class StorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(storage.SCHEMA)

    def test_store_and_fetch_fixtures(self) -> None:
        stored = storage.store_fixtures_payload(self.conn, FIXTURES_PAYLOAD, league_id=39, season=2024)
        self.assertEqual(stored, 1)

        rows = storage.fetch_season_fixtures(self.conn, league_id=39, season=2024)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["matchday"], 1)
        self.assertEqual(rows[0]["home_goals"], 2)

        team_names = storage.fetch_team_names(self.conn)
        self.assertEqual(team_names[42], "Arsenal")

    def test_upsert_is_idempotent(self) -> None:
        storage.store_fixtures_payload(self.conn, FIXTURES_PAYLOAD, league_id=39, season=2024)
        storage.store_fixtures_payload(self.conn, FIXTURES_PAYLOAD, league_id=39, season=2024)
        rows = storage.fetch_season_fixtures(self.conn, league_id=39, season=2024)
        self.assertEqual(len(rows), 1)

    def test_store_fixture_statistics_parses_percent_and_null_values(self) -> None:
        storage.store_fixtures_payload(self.conn, FIXTURES_PAYLOAD, league_id=39, season=2024)
        stored = storage.store_fixture_statistics_payload(self.conn, STATS_PAYLOAD, fixture_id=1001)
        self.assertEqual(stored, 1)

        self.conn.row_factory = sqlite3.Row
        row = self.conn.execute("SELECT * FROM fixture_stats WHERE fixture_id = 1001").fetchone()
        self.assertEqual(row["shots_total"], 10)
        self.assertEqual(row["possession_pct"], 55.0)
        self.assertEqual(row["expected_goals"], 1.8)
        self.assertIsNone(row["red_cards"])

    def test_fetch_fixtures_missing_stats(self) -> None:
        storage.store_fixtures_payload(self.conn, FIXTURES_PAYLOAD, league_id=39, season=2024)
        missing = storage.fetch_fixtures_missing_stats(self.conn, league_id=39, season=2024)
        self.assertEqual([row["id"] for row in missing], [1001])

        storage.store_fixture_statistics_payload(self.conn, STATS_PAYLOAD, fixture_id=1001)
        missing_after = storage.fetch_fixtures_missing_stats(self.conn, league_id=39, season=2024)
        self.assertEqual(missing_after, [])

    def test_fetch_teams_for_season(self) -> None:
        storage.store_fixtures_payload(self.conn, FIXTURES_PAYLOAD, league_id=39, season=2024)
        team_ids = storage.fetch_teams_for_season(self.conn, league_id=39, season=2024)
        self.assertEqual(set(team_ids), {42, 49})

    def test_store_players_payload_handles_mixed_height_weight_formats(self) -> None:
        # Real API responses are inconsistent: some players have "174 cm" / "60 kg",
        # others have bare "182" / "79" with no unit suffix.
        stored = storage.store_players_payload(self.conn, PLAYERS_PAYLOAD, league_id=39, season=2024)
        self.assertEqual(stored, 2)

        self.conn.row_factory = sqlite3.Row
        rows = {row["id"]: row for row in self.conn.execute("SELECT * FROM players")}
        self.assertEqual(rows[1161]["height_cm"], 182)
        self.assertEqual(rows[1161]["weight_kg"], 79)
        self.assertEqual(rows[278075]["height_cm"], 174)
        self.assertEqual(rows[278075]["weight_kg"], 60)

        stats = self.conn.execute(
            "SELECT * FROM player_season_stats WHERE player_id = 1161"
        ).fetchone()
        self.assertEqual(stats["goals"], 0)
        self.assertEqual(stats["assists"], 1)
        self.assertEqual(stats["appearances"], 13)

    def test_store_players_payload_skips_player_with_no_stats_block(self) -> None:
        # A player with an entirely null statistics block (unused squad player)
        # should still be upserted into players, but contribute no stats row
        # if team id is missing - guards against the all-null sample we saw live.
        payload = {
            "response": [
                {
                    "player": {"id": 999, "name": "Benchwarmer", "height": None, "weight": None},
                    "statistics": [{"team": {"id": None}, "games": {}}],
                }
            ]
        }
        stored = storage.store_players_payload(self.conn, payload, league_id=39, season=2024)
        self.assertEqual(stored, 0)
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM players WHERE id = 999").fetchone())

    def test_store_team_statistics_payload(self) -> None:
        stored = storage.store_team_statistics_payload(
            self.conn, TEAM_STATS_PAYLOAD, league_id=39, season=2024, team_id=42
        )
        self.assertTrue(stored)

        self.conn.row_factory = sqlite3.Row
        row = self.conn.execute(
            "SELECT * FROM team_season_stats WHERE league_id = 39 AND season = 2024 AND team_id = 42"
        ).fetchone()
        self.assertEqual(row["wins_total"], 28)
        self.assertEqual(row["goals_for_total"], 91)
        self.assertEqual(row["clean_sheets_total"], 18)
        self.assertIn('"0.5"', row["goals_for_under_over_json"])

    def test_fetch_teams_with_season_stats(self) -> None:
        self.assertEqual(storage.fetch_teams_with_season_stats(self.conn, league_id=39, season=2024), set())
        storage.store_team_statistics_payload(self.conn, TEAM_STATS_PAYLOAD, league_id=39, season=2024, team_id=42)
        self.assertEqual(storage.fetch_teams_with_season_stats(self.conn, league_id=39, season=2024), {42})


PLAYERS_PAYLOAD = {
    "response": [
        {
            "player": {
                "id": 1161,
                "name": "E. Smith Rowe",
                "height": "182",
                "weight": "79",
            },
            "statistics": [
                {
                    "team": {"id": 42, "name": "Arsenal"},
                    "games": {"appearences": 13, "lineups": 3, "minutes": 346, "position": "Midfielder", "rating": "6.775000"},
                    "shots": {"total": 11, "on": 7},
                    "goals": {"total": 0, "assists": 1},
                    "passes": {"total": 211, "key": 4},
                    "tackles": {"total": 4, "blocks": 1, "interceptions": 3},
                    "duels": {"total": 31, "won": 12},
                    "dribbles": {"attempts": 6, "success": 3},
                    "fouls": {"drawn": 2, "committed": 5},
                    "cards": {"yellow": 0, "red": 0},
                    "penalty": {"scored": 0, "missed": 0},
                }
            ],
        },
        {
            "player": {
                "id": 278075,
                "name": "C. Cirjan",
                "height": "174 cm",
                "weight": "60 kg",
            },
            "statistics": [
                {
                    "team": {"id": 42, "name": "Arsenal"},
                    "games": {"appearences": None, "lineups": None, "minutes": None, "position": "Midfielder", "rating": None},
                }
            ],
        },
    ],
    "paging": {"current": 1, "total": 1},
}

TEAM_STATS_PAYLOAD = {
    "response": {
        "form": "WWDWWDWWDW",
        "fixtures": {
            "played": {"total": 38},
            "wins": {"home": 15, "away": 13, "total": 28},
            "draws": {"home": 2, "away": 3, "total": 5},
            "loses": {"home": 2, "away": 3, "total": 5},
        },
        "goals": {
            "for": {
                "total": {"home": 48, "away": 43, "total": 91},
                "minute": {"0-15": {"total": 10, "percentage": "11.49%"}},
                "under_over": {"0.5": {"over": 33, "under": 5}},
            },
            "against": {
                "total": {"home": 16, "away": 13, "total": 29},
                "minute": {"0-15": {"total": 6, "percentage": "18.18%"}},
                "under_over": {"0.5": {"over": 20, "under": 18}},
            },
        },
        "biggest": {"streak": {"wins": 8, "draws": 1, "loses": 2}},
        "clean_sheet": {"home": 7, "away": 11, "total": 18},
        "failed_to_score": {"home": 2, "away": 3, "total": 5},
        "penalty": {"scored": {"total": 10}, "missed": {"total": 0}},
    }
}


if __name__ == "__main__":
    unittest.main()
