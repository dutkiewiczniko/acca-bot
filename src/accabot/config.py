from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    odds_api_key: str | None
    football_data_token: str | None
    api_football_key: str | None
    odds_regions: str = "uk"
    odds_markets: str = "h2h"
    odds_format: str = "decimal"


def load_settings() -> Settings:
    return Settings(
        odds_api_key=os.getenv("ODDS_API_KEY"),
        football_data_token=os.getenv("FOOTBALL_DATA_TOKEN"),
        api_football_key=os.getenv("API_FOOTBALL_KEY"),
        odds_regions=os.getenv("ODDS_REGIONS", "uk"),
        odds_markets=os.getenv("ODDS_MARKETS", "h2h"),
        odds_format=os.getenv("ODDS_FORMAT", "decimal"),
    )
