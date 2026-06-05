from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .http import get_json

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"


class EspnSoccerClient:
    def scoreboard(
        self,
        *,
        league: str = "fifa.world",
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        date_from = date_from or date.today()
        date_to = date_to or (date_from + timedelta(days=60))
        return get_json(
            f"{BASE_URL}/{league}/scoreboard",
            query={
                "dates": f"{date_from:%Y%m%d}-{date_to:%Y%m%d}",
                "limit": limit,
            },
        )


def world_cup_fixture_summaries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    fixtures: list[dict[str, Any]] = []
    for event in payload.get("events", []):
        competition = _first(event.get("competitions", []))
        competitors = competition.get("competitors", []) if competition else []
        home = _competitor(competitors, "home")
        away = _competitor(competitors, "away")
        venue = competition.get("venue", {}) if competition else {}
        fixtures.append(
            {
                "source": "ESPN",
                "fixture_id": event.get("id"),
                "kickoff": event.get("date"),
                "home_team": home.get("team", {}).get("displayName") or home.get("team", {}).get("name"),
                "away_team": away.get("team", {}).get("displayName") or away.get("team", {}).get("name"),
                "venue": venue.get("fullName"),
                "city": (venue.get("address") or {}).get("city"),
                "status": event.get("status", {}).get("type", {}).get("description"),
            }
        )
    return fixtures


def seeded_world_cup_fixtures(limit: int = 24) -> list[dict[str, Any]]:
    fixtures = [
        ("2026-06-11", "Mexico", "South Africa", "Group A", "Mexico City Stadium", "Mexico City"),
        ("2026-06-11", "Korea Republic", "Czechia", "Group A", "Estadio Guadalajara", "Guadalajara"),
        ("2026-06-12", "Canada", "Bosnia and Herzegovina", "Group B", "Toronto Stadium", "Toronto"),
        ("2026-06-12", "United States", "Paraguay", "Group D", "Los Angeles Stadium", "Los Angeles"),
        ("2026-06-13", "Haiti", "Scotland", "Group C", "Boston Stadium", "Boston"),
        ("2026-06-13", "Australia", "Turkiye", "Group D", "BC Place Vancouver", "Vancouver"),
        ("2026-06-13", "Brazil", "Morocco", "Group C", "New York New Jersey Stadium", "East Rutherford"),
        ("2026-06-13", "Qatar", "Switzerland", "Group B", "San Francisco Bay Area Stadium", "Santa Clara"),
        ("2026-06-14", "Cote d'Ivoire", "Ecuador", "Group E", "Philadelphia Stadium", "Philadelphia"),
        ("2026-06-14", "Germany", "Curacao", "Group E", "Houston Stadium", "Houston"),
        ("2026-06-14", "Netherlands", "Japan", "Group F", "Dallas Stadium", "Arlington"),
        ("2026-06-14", "Sweden", "Tunisia", "Group F", "Estadio Monterrey", "Monterrey"),
        ("2026-06-15", "Saudi Arabia", "Uruguay", "Group H", "Miami Stadium", "Miami"),
        ("2026-06-15", "Spain", "Cape Verde", "Group H", "Atlanta Stadium", "Atlanta"),
        ("2026-06-15", "IR Iran", "New Zealand", "Group G", "Los Angeles Stadium", "Los Angeles"),
        ("2026-06-15", "Belgium", "Egypt", "Group G", "Seattle Stadium", "Seattle"),
        ("2026-06-16", "France", "Senegal", "Group I", "New York New Jersey Stadium", "East Rutherford"),
        ("2026-06-16", "Iraq", "Norway", "Group I", "Boston Stadium", "Boston"),
        ("2026-06-16", "Argentina", "Algeria", "Group J", "Kansas City Stadium", "Kansas City"),
        ("2026-06-16", "Austria", "Jordan", "Group J", "San Francisco Bay Area Stadium", "Santa Clara"),
        ("2026-06-17", "Ghana", "Panama", "Group L", "Toronto Stadium", "Toronto"),
        ("2026-06-17", "England", "Croatia", "Group L", "Dallas Stadium", "Arlington"),
        ("2026-06-17", "Portugal", "DR Congo", "Group K", "Houston Stadium", "Houston"),
        ("2026-06-17", "Uzbekistan", "Colombia", "Group K", "Mexico City Stadium", "Mexico City"),
    ]
    return [
        {
            "source": "Bundled FIFA schedule seed",
            "fixture_id": None,
            "kickoff": kickoff,
            "home_team": home,
            "away_team": away,
            "venue": venue,
            "city": city,
            "status": stage,
        }
        for kickoff, home, away, stage, venue, city in fixtures[:limit]
    ]


def _first(items: list[dict[str, Any]]) -> dict[str, Any]:
    return items[0] if items else {}


def _competitor(competitors: list[dict[str, Any]], home_away: str) -> dict[str, Any]:
    for competitor in competitors:
        if competitor.get("homeAway") == home_away:
            return competitor
    return {}
