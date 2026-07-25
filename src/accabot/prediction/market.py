from __future__ import annotations

import sqlite3


def _demargin(home: float | None, draw: float | None, away: float | None) -> dict[str, float] | None:
    """Convert a home/draw/away decimal-odds triple into de-margined probabilities.

    Uses the basic proportional (normalization) method: take each outcome's
    implied probability (1/odds), then divide by their sum so they add to 1.
    The sum before normalizing exceeds 1 by the bookmaker's overround (margin);
    dividing it out is the standard first-order estimate of the "true" price.
    """
    if not home or not draw or not away:
        return None
    implied = {"home": 1.0 / home, "draw": 1.0 / draw, "away": 1.0 / away}
    total = sum(implied.values())
    if total <= 0:
        return None
    return {k: v / total for k, v in implied.items()}


def load_market_probs(
    conn: sqlite3.Connection,
    *,
    league_id: int = 39,
    prefer: str = "pinnacle",
) -> dict[int, dict[str, float]]:
    """fixture_id -> de-margined {home, draw, away} market probabilities.

    ``prefer`` picks which odds source to try first per fixture; it falls back
    to the other when the preferred one is missing. Pinnacle is the sharper
    (lowest-margin) book and the better "true price" proxy, but only present
    from 2012/13 on, so market average is the fallback for earlier seasons.
    """
    rows = conn.execute(
        """
        SELECT ho.fixture_id,
               ho.pinnacle_home, ho.pinnacle_draw, ho.pinnacle_away,
               ho.avg_home, ho.avg_draw, ho.avg_away
        FROM historical_odds ho
        JOIN fixtures f ON f.id = ho.fixture_id
        WHERE f.league_id = ?
        """,
        (league_id,),
    ).fetchall()

    out: dict[int, dict[str, float]] = {}
    for fixture_id, ph, pd, pa, ah, ad, aa in rows:
        pinnacle = (ph, pd, pa)
        average = (ah, ad, aa)
        first, second = (pinnacle, average) if prefer == "pinnacle" else (average, pinnacle)
        probs = _demargin(*first) or _demargin(*second)
        if probs is not None:
            out[fixture_id] = probs
    return out
