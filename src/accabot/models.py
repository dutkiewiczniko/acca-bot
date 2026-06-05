from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Selection:
    event_id: str
    sport_key: str
    commence_time: datetime | None
    home_team: str
    away_team: str
    market: str
    outcome: str
    bookmaker: str
    odds: float
    implied_probability: float
    model_probability: float
    edge: float

    @property
    def label(self) -> str:
        return f"{self.home_team} vs {self.away_team}: {self.outcome} @ {self.odds:g}"


@dataclass(frozen=True)
class Accumulator:
    selections: tuple[Selection, ...]
    decimal_odds: float
    model_probability: float
    expected_value: float

    @property
    def legs(self) -> int:
        return len(self.selections)
