from __future__ import annotations

import time
from datetime import date
from typing import Any

from .http import RateLimitedError, get_json
from .ratelimit import RateLimiter

BASE_URL = "https://v3.football.api-sports.io"
MAX_RATE_LIMIT_RETRIES = 3
RATE_LIMIT_RETRY_SECONDS = 65


class ApiFootballClient:
    def __init__(self, api_key: str, *, rate_limiter: RateLimiter | None = None) -> None:
        self.headers = {"x-apisports-key": api_key}
        self.rate_limiter = rate_limiter

    def _get(self, path: str, query: dict[str, Any]) -> dict[str, Any]:
        attempt = 0
        while True:
            if self.rate_limiter is not None:
                self.rate_limiter.acquire()
            try:
                result = get_json(f"{BASE_URL}{path}", query=query, headers=self.headers)
            except RateLimitedError:
                # Our own pacing thought this was safe, but the server disagreed - it was
                # too optimistic. Back off, learn a slower pace for future calls, and retry
                # a few times before giving up (a failed request shouldn't burn daily budget).
                if self.rate_limiter is not None:
                    self.rate_limiter.throttle_down()
                attempt += 1
                if attempt > MAX_RATE_LIMIT_RETRIES:
                    raise
                time.sleep(RATE_LIMIT_RETRY_SECONDS)
                continue
            if self.rate_limiter is not None:
                self.rate_limiter.record_call()
            return result

    def injuries(
        self,
        *,
        fixture: int | None = None,
        team: int | None = None,
        player: int | None = None,
        league: int | None = None,
        season: int | None = None,
        match_date: date | None = None,
    ) -> dict[str, Any]:
        return self._get(
            "/injuries",
            {
                "fixture": fixture,
                "team": team,
                "player": player,
                "league": league,
                "season": season,
                "date": match_date.isoformat() if match_date else None,
            },
        )

    def lineups(
        self,
        *,
        fixture: int,
        team: int | None = None,
        player: int | None = None,
    ) -> dict[str, Any]:
        return self._get("/fixtures/lineups", {"fixture": fixture, "team": team, "player": player})

    def fixtures(
        self,
        *,
        league: int | None = None,
        season: int | None = None,
        team: int | None = None,
        next_count: int | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> dict[str, Any]:
        return self._get(
            "/fixtures",
            {
                "league": league,
                "season": season,
                "team": team,
                "next": next_count,
                "from": from_date.isoformat() if from_date else None,
                "to": to_date.isoformat() if to_date else None,
            },
        )

    def fixture_statistics(self, *, fixture: int) -> dict[str, Any]:
        return self._get("/fixtures/statistics", {"fixture": fixture})

    def leagues(
        self,
        *,
        search: str | None = None,
        code: str | None = None,
        season: int | None = None,
    ) -> dict[str, Any]:
        return self._get("/leagues", {"search": search, "code": code, "season": season})

    def standings(self, *, league: int, season: int) -> dict[str, Any]:
        return self._get("/standings", {"league": league, "season": season})
