from __future__ import annotations

from .data import Match, load_matches, team_names
from .poisson import DixonColesModel
from .service import predict
from .teams import build_team_name_index, resolve_team_id

__all__ = [
    "Match",
    "load_matches",
    "team_names",
    "DixonColesModel",
    "predict",
    "build_team_name_index",
    "resolve_team_id",
]
