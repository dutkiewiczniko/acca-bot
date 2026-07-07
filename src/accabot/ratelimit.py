from __future__ import annotations

import json
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path


class DailyBudgetExceeded(RuntimeError):
    """Raised when the persisted daily request count has hit the plan's cap."""


class RateLimiter:
    """Paces and persists request counts against API-Football's plan caps.

    The free plan allows 10 requests/minute and 100/day, reset at 00:00 UTC.
    A full historical backfill needs far more than 100 requests, so the daily
    count is persisted to disk: running the backfill command again tomorrow
    continues where it left off instead of re-spending an exhausted budget.
    """

    def __init__(self, state_path: Path, *, per_minute: int = 10, per_day: int = 100) -> None:
        self.state_path = state_path
        self.per_day = per_day
        self._recent_calls: deque[float] = deque()
        self._today = self._today_utc()
        state = self._load_state()
        self._count_today = state["count"] if state.get("date") == self._today else 0
        # Adopt a previously learned, more conservative per-minute pace if one was persisted -
        # a live 429 from the server is stronger evidence than our starting assumption.
        self.per_minute = min(per_minute, state.get("per_minute", per_minute))

    def remaining_today(self) -> int:
        self._roll_day_if_needed()
        return max(0, self.per_day - self._count_today)

    def throttle_down(self, *, floor: int = 1) -> None:
        """Permanently (until process restart or new learning) lower the assumed per-minute pace.

        Called after the server itself rejects a request with HTTP 429, which means our
        configured per_minute was too optimistic. Persisted so future runs start conservative too.
        """
        self.per_minute = max(floor, self.per_minute - 2)
        self._save()

    def acquire(self) -> None:
        """Block until a request is safe to send, or raise if today's budget is spent."""
        self._roll_day_if_needed()
        if self._count_today >= self.per_day:
            raise DailyBudgetExceeded(
                f"Used all {self.per_day} API-Football requests allotted for {self._today} (UTC)."
            )
        now = time.monotonic()
        while self._recent_calls and now - self._recent_calls[0] > 60:
            self._recent_calls.popleft()
        if len(self._recent_calls) >= self.per_minute:
            sleep_for = 60 - (now - self._recent_calls[0]) + 0.05
            if sleep_for > 0:
                time.sleep(sleep_for)
        self._recent_calls.append(time.monotonic())

    def record_call(self) -> None:
        """Call once a request has actually been sent, to persist budget usage."""
        self._roll_day_if_needed()
        self._count_today += 1
        self._save()

    def _today_utc(self) -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def _roll_day_if_needed(self) -> None:
        today = self._today_utc()
        if today != self._today:
            self._today = today
            self._count_today = 0
            self._save()

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps({"date": self._today, "count": self._count_today, "per_minute": self.per_minute})
        )
