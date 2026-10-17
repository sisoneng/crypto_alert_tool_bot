from pathlib import Path

import pytest

from sec_sentinel.http import RateLimiter, SecClient

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeClock:
    """Monotonic clock whose sleep() advances time instantly."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class RecordingSender:
    def __init__(self, fail_on: int | None = None) -> None:
        self.messages: list[str] = []
        self._fail_on = fail_on

    def send(self, text: str) -> None:
        if self._fail_on is not None and len(self.messages) + 1 == self._fail_on:
            raise RuntimeError("send failed")
        self.messages.append(text)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def sec(clock: FakeClock):
    limiter = RateLimiter(5, clock=clock, sleep=clock.sleep)
    client = SecClient("sec-sentinel test@example.com", limiter=limiter, sleep=clock.sleep, jitter=lambda: 0.0)
    yield client
    client.close()
