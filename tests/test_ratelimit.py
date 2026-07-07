import tempfile
import unittest
from pathlib import Path

from accabot.ratelimit import DailyBudgetExceeded, RateLimiter


class RateLimiterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_path = Path(self.tmpdir.name) / "usage.json"

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_remaining_today_decreases_with_recorded_calls(self) -> None:
        limiter = RateLimiter(self.state_path, per_minute=1000, per_day=5)
        self.assertEqual(limiter.remaining_today(), 5)
        limiter.acquire()
        limiter.record_call()
        self.assertEqual(limiter.remaining_today(), 4)

    def test_raises_once_daily_budget_spent(self) -> None:
        limiter = RateLimiter(self.state_path, per_minute=1000, per_day=2)
        limiter.acquire()
        limiter.record_call()
        limiter.acquire()
        limiter.record_call()
        with self.assertRaises(DailyBudgetExceeded):
            limiter.acquire()

    def test_persists_count_across_instances_same_day(self) -> None:
        first = RateLimiter(self.state_path, per_minute=1000, per_day=10)
        first.acquire()
        first.record_call()
        first.acquire()
        first.record_call()

        second = RateLimiter(self.state_path, per_minute=1000, per_day=10)
        self.assertEqual(second.remaining_today(), 8)


if __name__ == "__main__":
    unittest.main()
