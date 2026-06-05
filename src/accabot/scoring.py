from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import Accumulator, Selection


def implied_probability(decimal_odds: float) -> float:
    if decimal_odds <= 1:
        raise ValueError("Decimal odds must be greater than 1.")
    return 1 / decimal_odds


def conservative_model_probability(decimal_odds: float, *, edge_hint: float = 0.02) -> float:
    """Placeholder model: small uplift over market-implied probability, capped conservatively."""
    implied = implied_probability(decimal_odds)
    return min(0.92, implied + edge_hint)


def extract_selections(events: list[dict[str, Any]], *, sport_key: str) -> list[Selection]:
    selections: list[Selection] = []
    for event in events:
        commence_time = _parse_datetime(event.get("commence_time"))
        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    price = float(outcome["price"])
                    model_probability = conservative_model_probability(price)
                    implied = implied_probability(price)
                    selections.append(
                        Selection(
                            event_id=str(event["id"]),
                            sport_key=sport_key,
                            commence_time=commence_time,
                            home_team=str(event.get("home_team", "")),
                            away_team=str(event.get("away_team", "")),
                            market="h2h",
                            outcome=str(outcome["name"]),
                            bookmaker=str(bookmaker["title"]),
                            odds=price,
                            implied_probability=implied,
                            model_probability=model_probability,
                            edge=model_probability - implied,
                        )
                    )
    return selections


def build_accumulator(selections: list[Selection], *, legs: int = 4) -> Accumulator:
    if legs < 1:
        raise ValueError("Accumulator must contain at least one leg.")

    selected: list[Selection] = []
    used_events: set[str] = set()
    for selection in sorted(selections, key=lambda item: item.edge, reverse=True):
        if selection.event_id in used_events:
            continue
        selected.append(selection)
        used_events.add(selection.event_id)
        if len(selected) == legs:
            break

    decimal_odds = 1.0
    model_probability = 1.0
    for selection in selected:
        decimal_odds *= selection.odds
        model_probability *= selection.model_probability

    expected_value = (decimal_odds * model_probability) - 1
    return Accumulator(tuple(selected), decimal_odds, model_probability, expected_value)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
