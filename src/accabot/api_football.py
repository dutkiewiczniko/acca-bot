from __future__ import annotations

from datetime import date
from typing import Any

from .http import get_json

BASE_URL = "https://v3.football.api-sports.io"


class ApiFootballClient:
    def __init__(self, api_key: str) -> None:
        self.headers = {"x-apisports-key": api_key}

    def injuries(
        self,
        *,
        fixture: int | None = None,
        team: int | None = None,
        player: int | None = None,
        league: int | None = None,
        season: int | None = None,
        match_date: date | None = None,
    ) -> dict[str, Any]:
        return get_json(
            f"{BASE_URL}/injuries",
            query={
                "fixture": fixture,
                "team": team,
                "player": player,
                "league": league,
                "season": season,
                "date": match_date.isoformat() if match_date else None,
            },
            headers=self.headers,
        )

    def lineups(
        self,
        *,
        fixture: int,
        team: int | None = None,
        player: int | None = None,
    ) -> dict[str, Any]:
        return get_json(
            f"{BASE_URL}/fixtures/lineups",
            query={"fixture": fixture, "team": team, "player": player},
            headers=self.headers,
        )

    def fixtures(
        self,
        *,
        league: int | None = None,
        season: int | None = None,
        team: int | None = None,
        next_count: int | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> dict[str, Any]:
        return get_json(
            f"{BASE_URL}/fixtures",
            query={
                "league": league,
                "season": season,
                "team": team,
                "next": next_count,
                "from": from_date.isoformat() if from_date else None,
                "to": to_date.isoformat() if to_date else None,
            },
            headers=self.headers,
        )
