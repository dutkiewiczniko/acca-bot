from __future__ import annotations

from datetime import date
from typing import Any

from .http import get_json

BASE_URL = "https://api.football-data.org/v4"


class FootballDataClient:
    def __init__(self, token: str) -> None:
        self.headers = {"X-Auth-Token": token}

    def competitions(self) -> dict[str, Any]:
        return get_json(f"{BASE_URL}/competitions", headers=self.headers)

    def matches(
        self,
        *,
        competition: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        return get_json(
            f"{BASE_URL}/matches",
            query={
                "competitions": competition,
                "dateFrom": date_from.isoformat() if date_from else None,
                "dateTo": date_to.isoformat() if date_to else None,
                "status": status,
            },
            headers=self.headers,
        )

    def team(self, team_id: int) -> dict[str, Any]:
        return get_json(f"{BASE_URL}/teams/{team_id}", headers=self.headers)
