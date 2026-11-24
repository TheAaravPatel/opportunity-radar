import httpx
import pytest

from radar.http import BlockedError, Http


def client(handler, sleeps):
    return Http("ua", transport=httpx.MockTransport(handler), sleep=sleeps.append, min_delay=0, max_retries=3)


def test_retries_then_succeeds():
    attempts, sleeps = [], []

    def handler(request):
        attempts.append(1)
        return httpx.Response(503) if len(attempts) < 3 else httpx.Response(200, json={"ok": True})

    assert client(handler, sleeps).get_json("https://example.com/x") == {"ok": True}
    assert len(attempts) == 3
    assert len(sleeps) == 2            # backed off twice


def test_honors_retry_after():
    attempts, sleeps = [], []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "30"})
        return httpx.Response(200, text="ok")

    client(handler, sleeps).get("https://example.com/x")
    assert sleeps[0] >= 30


def test_bot_challenge_stops_immediately():
    attempts, sleeps = [], []

    def handler(request):
        attempts.append(1)
        return httpx.Response(429, headers={"cf-mitigated": "challenge"})

    with pytest.raises(BlockedError):
        client(handler, sleeps).get("https://tenant.wd5.myworkdayjobs.com/x")
    assert len(attempts) == 1          # no hammering


def test_gives_up_after_max_retries():
    sleeps = []
    with pytest.raises(httpx.HTTPStatusError):
        client(lambda request: httpx.Response(500), sleeps).get("https://example.com/x")


def test_per_host_delay_uses_suffix():
    http = Http("ua", min_delay=1.0, per_host_delay={"myworkdayjobs.com": 3.0})
    assert http._delay_for("rb.wd5.myworkdayjobs.com") == 3.0
    assert http._delay_for("example.com") == 1.0
