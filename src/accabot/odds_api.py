from __future__ import annotations

from typing import Any

from .http import get_json

BASE_URL = "https://api.the-odds-api.com/v4"


class OddsApiClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def sports(self, *, all_sports: bool = False) -> list[dict[str, Any]]:
        return get_json(
            f"{BASE_URL}/sports",
            query={"apiKey": self.api_key, "all": "true" if all_sports else None},
        )

    def odds(
        self,
        sport_key: str,
        *,
        regions: str = "uk",
        markets: str = "h2h",
        odds_format: str = "decimal",
    ) -> list[dict[str, Any]]:
        return get_json(
            f"{BASE_URL}/sports/{sport_key}/odds",
            query={
                "apiKey": self.api_key,
                "regions": regions,
                "markets": markets,
                "oddsFormat": odds_format,
            },
        )
