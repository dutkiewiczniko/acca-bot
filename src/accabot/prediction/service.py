from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from .data import load_matches
from .poisson import DixonColesModel

DEFAULT_DB_PATH = Path("data/acca-bot.sqlite3")
DEFAULT_XI = 0.0018


@lru_cache(maxsize=32)
def _cached_model(db_path: str, league_id: int, as_of_day: str, xi: float) -> DixonColesModel:
    """Fit is cached per (db, league, calendar day, xi) so predicting several
    fixtures on the same matchday only fits the model once."""
    conn = sqlite3.connect(db_path)
    try:
        as_of = datetime.fromisoformat(as_of_day).replace(tzinfo=timezone.utc)
        matches = [m for m in load_matches(conn, league_id=league_id) if m.kickoff < as_of]
        team_ids = sorted({t for m in matches for t in (m.home_id, m.away_id)})
        model = DixonColesModel(xi=xi)
        model.fit(matches, team_ids=team_ids, as_of=as_of)
        return model
    finally:
        conn.close()


def predict(
    home_id: int,
    away_id: int,
    as_of_date: datetime,
    *,
    db_path: Path | str = DEFAULT_DB_PATH,
    league_id: int = 39,
    xi: float = DEFAULT_XI,
) -> dict:
    """Fit a time-weighted Dixon-Coles model on all fixtures strictly before
    ``as_of_date`` and predict the outcome distribution for home_id vs away_id.

    The fit is cached per calendar day so repeated calls for the same
    matchday (e.g. building an accumulator across several fixtures) reuse
    one fitted model instead of refitting per fixture.
    """
    if as_of_date.tzinfo is None:
        as_of_date = as_of_date.replace(tzinfo=timezone.utc)
    model = _cached_model(str(db_path), league_id, as_of_date.date().isoformat(), xi)
    result = model.predict(home_id, away_id)
    result["as_of"] = as_of_date.isoformat()
    result["fitted_on_matches"] = model.n_matches_fit
    return result
