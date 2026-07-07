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


if __name__ == "__main__":
    unittest.main()
