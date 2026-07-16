from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Load KEY=value lines from a local .env file into os.environ.

    Real environment variables always win over .env values. Lines that are
    blank, comments, or malformed are ignored.
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    odds_api_key: str | None
    football_data_token: str | None
    api_football_key: str | None
    odds_regions: str = "uk"
    odds_markets: str = "h2h"
    odds_format: str = "decimal"


def load_settings() -> Settings:
    _load_dotenv()
    return Settings(
        odds_api_key=os.getenv("ODDS_API_KEY"),
        football_data_token=os.getenv("FOOTBALL_DATA_TOKEN"),
        api_football_key=os.getenv("API_FOOTBALL_KEY"),
        odds_regions=os.getenv("ODDS_REGIONS", "uk"),
        odds_markets=os.getenv("ODDS_MARKETS", "h2h"),
        odds_format=os.getenv("ODDS_FORMAT", "decimal"),
    )
