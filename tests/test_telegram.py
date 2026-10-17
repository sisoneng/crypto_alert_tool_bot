import json

import httpx
import pytest
import respx

from conftest import fixture_bytes
from sec_sentinel.telegram import TelegramError, TelegramSender

TOKEN = "123456:SECRET-TOKEN"
URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
OK = httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})


@pytest.fixture
def sender(clock):
    s = TelegramSender(TOKEN, "-100123", clock=clock, sleep=clock.sleep)
    yield s
    s.close()


@respx.mock
def test_send_payload(sender):
    route = respx.post(URL).mock(return_value=OK)
    sender.send("<b>hi</b>")
    body = json.loads(route.calls.last.request.content)
    assert body["chat_id"] == "-100123"
    assert body["text"] == "<b>hi</b>"
    assert body["parse_mode"] == "HTML"


@respx.mock
def test_429_retries_after_retry_after(sender, clock):
    route = respx.post(URL).mock(
        side_effect=[httpx.Response(429, content=fixture_bytes("telegram_429.json")), OK]
    )
    sender.send("hello")
    assert route.call_count == 2
    assert 3.0 in clock.sleeps  # retry_after from the fixture


@respx.mock
def test_429_gives_up_after_max_tries(sender):
    respx.post(URL).mock(return_value=httpx.Response(429, content=fixture_bytes("telegram_429.json")))
    with pytest.raises(TelegramError):
        sender.send("hello")


@respx.mock
def test_one_message_per_second(sender, clock):
    times = []
    respx.post(URL).mock(side_effect=lambda request: times.append(clock()) or OK)
    for _ in range(3):
        sender.send("x")
    assert [b - a for a, b in zip(times, times[1:])] == [1.0, 1.0]


@respx.mock
def test_error_does_not_leak_token(sender):
    respx.post(URL).mock(
        return_value=httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})
    )
    with pytest.raises(TelegramError) as exc_info:
        sender.send("x")
    assert "chat not found" in str(exc_info.value)
    assert TOKEN not in str(exc_info.value)
