import httpx
import pytest
import respx

from sec_sentinel.http import RateLimiter, SecError

URL = "https://www.sec.gov/files/company_tickers.json"


def test_rate_limiter_spaces_calls(clock):
    limiter = RateLimiter(5, clock=clock, sleep=clock.sleep)
    times = []
    for _ in range(6):
        limiter.wait()
        times.append(clock())
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(gap >= 0.2 - 1e-9 for gap in gaps)
    assert times[-1] - times[0] == pytest.approx(1.0)  # 5 gaps of 0.2s


def test_rate_limiter_no_wait_when_idle(clock):
    limiter = RateLimiter(5, clock=clock, sleep=clock.sleep)
    limiter.wait()
    clock.t += 10
    limiter.wait()
    assert clock.sleeps == []


@respx.mock
def test_sends_user_agent(sec):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={}))
    sec.get(URL)
    assert route.calls.last.request.headers["User-Agent"] == "sec-sentinel test@example.com"


@respx.mock
def test_retries_429_and_5xx_with_backoff(sec, clock):
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(429), httpx.Response(200, json={"ok": 1})]
    )
    assert sec.get(URL).json() == {"ok": 1}
    assert route.call_count == 3
    backoffs = [s for s in clock.sleeps if s >= 1.0]
    assert backoffs == [1.0, 2.0]


@respx.mock
def test_gives_up_after_three_tries(sec):
    route = respx.get(URL).mock(return_value=httpx.Response(500))
    with pytest.raises(SecError):
        sec.get(URL)
    assert route.call_count == 3


@respx.mock
def test_does_not_retry_404(sec):
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        sec.get(URL)
    assert route.call_count == 1
