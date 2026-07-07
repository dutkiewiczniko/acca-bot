import unittest
from unittest import mock

from accabot import api_football
from accabot.api_football import ApiFootballClient
from accabot.http import RateLimitedError


class FakeRateLimiter:
    def __init__(self) -> None:
        self.acquire_calls = 0
        self.record_calls = 0
        self.throttle_down_calls = 0
        self.per_minute = 10

    def acquire(self) -> None:
        self.acquire_calls += 1

    def record_call(self) -> None:
        self.record_calls += 1

    def throttle_down(self) -> None:
        self.throttle_down_calls += 1


class RetryOnRateLimitTest(unittest.TestCase):
    def test_retries_after_429_then_succeeds_without_sleeping_test_time(self) -> None:
        limiter = FakeRateLimiter()
        client = ApiFootballClient("key", rate_limiter=limiter)

        responses = [RateLimitedError("HTTP 429"), {"response": ["ok"]}]

        def fake_get_json(*args, **kwargs):
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with mock.patch.object(api_football, "get_json", side_effect=fake_get_json), mock.patch.object(
            api_football.time, "sleep"
        ) as fake_sleep:
            result = client._get("/fixtures", {})

        self.assertEqual(result, {"response": ["ok"]})
        self.assertEqual(limiter.throttle_down_calls, 1)
        self.assertEqual(limiter.record_calls, 1)
        fake_sleep.assert_called_once()

    def test_gives_up_after_max_retries(self) -> None:
        limiter = FakeRateLimiter()
        client = ApiFootballClient("key", rate_limiter=limiter)

        with mock.patch.object(api_football, "get_json", side_effect=RateLimitedError("HTTP 429")), mock.patch.object(
            api_football.time, "sleep"
        ):
            with self.assertRaises(RateLimitedError):
                client._get("/fixtures", {})

        self.assertEqual(limiter.throttle_down_calls, api_football.MAX_RATE_LIMIT_RETRIES + 1)
        self.assertEqual(limiter.record_calls, 0)


if __name__ == "__main__":
    unittest.main()
