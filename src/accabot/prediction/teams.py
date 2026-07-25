from __future__ import annotations

import re
import sqlite3

# Bookmaker / Odds API feeds tend to use longer or differently-abbreviated club
# names than API-Football (which is what populates the `teams` table). Map the
# common variants seen in the-odds-api.com's `soccer_epl` feed onto the DB name.
_ALIASES: dict[str, str] = {
    "manchester united": "manchester united",
    "man united": "manchester united",
    "man utd": "manchester united",
    "manchester city": "manchester city",
    "man city": "manchester city",
    "newcastle united": "newcastle",
    "newcastle utd": "newcastle",
    "tottenham hotspur": "tottenham",
    "spurs": "tottenham",
    "wolverhampton wanderers": "wolves",
    "wolverhampton": "wolves",
    "brighton and hove albion": "brighton",
    "brighton & hove albion": "brighton",
    "leicester city": "leicester",
    "hull city": "hull city",
    "hull": "hull city",
    "sheffield united": "sheffield utd",
    "west bromwich albion": "west brom",
    "nottm forest": "nottingham forest",
    "nott'm forest": "nottingham forest",
    "queens park rangers": "qpr",
    "stoke": "stoke city",
    "west ham united": "west ham",
    "leeds united": "leeds",
    "afc bournemouth": "bournemouth",
    "cardiff city": "cardiff",
    "norwich city": "norwich",
    "swansea city": "swansea",
}


def _normalize(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return _ALIASES.get(cleaned, cleaned)


def build_team_name_index(conn: sqlite3.Connection) -> dict[str, int]:
    """Map normalized team names (including known bookmaker aliases) to team_id."""
    rows = conn.execute("SELECT id, name FROM teams").fetchall()
    index: dict[str, int] = {}
    for team_id, name in rows:
        index[_normalize(name)] = team_id
    return index


def resolve_team_id(name: str, index: dict[str, int]) -> int | None:
    return index.get(_normalize(name))
