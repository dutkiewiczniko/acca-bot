from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from .models import Accumulator, Selection
from .prediction import build_team_name_index, predict as model_predict, resolve_team_id

DEFAULT_MODEL_DB_PATH = Path("data/acca-bot.sqlite3")
DEFAULT_MODEL_LEAGUE_ID = 39


def implied_probability(decimal_odds: float) -> float:
    if decimal_odds <= 1:
        raise ValueError("Decimal odds must be greater than 1.")
    return 1 / decimal_odds


def conservative_model_probability(decimal_odds: float, *, edge_hint: float = 0.02) -> float:
    """Fallback used when the Dixon-Coles model can't price a selection (e.g. an
    unrecognized team name, or the fixtures database is unavailable): a small
    uplift over market-implied probability, capped conservatively."""
    implied = implied_probability(decimal_odds)
    return min(0.92, implied + edge_hint)


@lru_cache(maxsize=8)
def _team_name_index(db_path: str) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        return build_team_name_index(conn)
    finally:
        conn.close()


def _model_probability_for_outcome(
    *,
    home_team: str,
    away_team: str,
    outcome: str,
    commence_time: datetime | None,
    db_path: Path,
    league_id: int,
) -> float | None:
    """Look up the Dixon-Coles model's probability for one h2h outcome.

    Returns None (letting the caller fall back to the placeholder) if the DB is
    missing, the teams can't be resolved, or the outcome isn't a recognized
    home/away/draw label.
    """
    if not Path(db_path).is_file():
        return None
    index = _team_name_index(str(db_path))
    home_id = resolve_team_id(home_team, index)
    away_id = resolve_team_id(away_team, index)
    if home_id is None or away_id is None:
        return None

    if outcome == home_team:
        key = "home"
    elif outcome == away_team:
        key = "away"
    elif outcome.lower() == "draw":
        key = "draw"
    else:
        return None

    as_of = commence_time or datetime.now(timezone.utc)
    prediction = model_predict(home_id, away_id, as_of, db_path=db_path, league_id=league_id)
    return prediction[key]


def extract_selections(
    events: list[dict[str, Any]],
    *,
    sport_key: str,
    db_path: Path | str = DEFAULT_MODEL_DB_PATH,
    league_id: int = DEFAULT_MODEL_LEAGUE_ID,
) -> list[Selection]:
    db_path = Path(db_path)
    selections: list[Selection] = []
    for event in events:
        commence_time = _parse_datetime(event.get("commence_time"))
        home_team = str(event.get("home_team", ""))
        away_team = str(event.get("away_team", ""))
        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    price = float(outcome["price"])
                    outcome_name = str(outcome["name"])
                    model_probability = _model_probability_for_outcome(
                        home_team=home_team,
                        away_team=away_team,
                        outcome=outcome_name,
                        commence_time=commence_time,
                        db_path=db_path,
                        league_id=league_id,
                    )
                    if model_probability is None:
                        model_probability = conservative_model_probability(price)
                    implied = implied_probability(price)
                    selections.append(
                        Selection(
                            event_id=str(event["id"]),
                            sport_key=sport_key,
                            commence_time=commence_time,
                            home_team=home_team,
                            away_team=away_team,
                            market="h2h",
                            outcome=outcome_name,
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
