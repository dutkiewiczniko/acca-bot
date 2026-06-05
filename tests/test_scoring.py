import unittest

from accabot.scoring import build_accumulator, extract_selections, implied_probability


class ScoringTest(unittest.TestCase):
    def test_implied_probability_from_decimal_odds(self) -> None:
        self.assertEqual(implied_probability(2.0), 0.5)

    def test_build_accumulator_uses_one_selection_per_event(self) -> None:
        selections = extract_selections(
            [
                {
                    "id": "event-1",
                    "sport_key": "soccer_epl",
                    "commence_time": "2026-06-10T19:00:00Z",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "bookmakers": [
                        {
                            "title": "Book A",
                            "markets": [
                                {
                                    "key": "h2h",
                                    "outcomes": [
                                        {"name": "Arsenal", "price": 1.8},
                                        {"name": "Chelsea", "price": 4.2},
                                    ],
                                }
                            ],
                        }
                    ],
                },
                {
                    "id": "event-2",
                    "sport_key": "soccer_epl",
                    "commence_time": "2026-06-11T19:00:00Z",
                    "home_team": "Liverpool",
                    "away_team": "Everton",
                    "bookmakers": [
                        {
                            "title": "Book A",
                            "markets": [
                                {
                                    "key": "h2h",
                                    "outcomes": [
                                        {"name": "Liverpool", "price": 1.5},
                                        {"name": "Everton", "price": 6.0},
                                    ],
                                }
                            ],
                        }
                    ],
                },
            ],
            sport_key="soccer_epl",
        )

        acca = build_accumulator(selections, legs=2)

        self.assertEqual(acca.legs, 2)
        self.assertEqual(len({selection.event_id for selection in acca.selections}), 2)
        self.assertGreater(acca.decimal_odds, 1)


if __name__ == "__main__":
    unittest.main()
