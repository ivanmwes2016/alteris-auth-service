import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.helpers import captcha, rate_limit


def limiter_settings(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "REDIS_URL": None,
        "REDIS_TOKEN": None,
        "RATE_LIMITING_ENABLED": True,
        "RATE_LIMITS": {},
        "CLIENT_IP_HEADER": "cf-connecting-ip",
        "TRUSTED_PROXY_HOPS": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def _isolated_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """
    Never reach the Upstash instance in .env or Cloudflare, and start every test with
    empty counts. Turnstile is off unless a test turns it on.
    """
    monkeypatch.setattr(rate_limit, "get_settings", limiter_settings)
    monkeypatch.setattr(captcha, "get_settings", lambda: SimpleNamespace(TURNSTILE_SECRET_KEY=None))
    monkeypatch.setattr(rate_limit, "_http", None)
    rate_limit.reset_local_state()
    yield
    rate_limit.reset_local_state()


class FakeUpstash:
    """Upstash's REST API with the sliding-window script's semantics, in memory."""

    def __init__(self) -> None:
        self.sets: dict[str, list[int]] = {}
        self.requests: list[httpx.Request] = []
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            return httpx.Response(503)
        command = json.loads(request.content)
        if command[0] == "DEL":
            return httpx.Response(200, json={"result": int(bool(self.sets.pop(command[1], 0)))})
        _eval, _script, _numkeys, key, now, window, limit, _member = command
        now, window, limit = int(now), int(window), int(limit)
        hits = [t for t in self.sets.get(key, []) if t > now - window]
        if len(hits) < limit:
            self.sets[key] = [*hits, now]
            return httpx.Response(200, json={"result": 0})
        self.sets[key] = hits
        return httpx.Response(200, json={"result": max(1, hits[0] + window - now)})


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Seconds; drives both the Redis timestamps and the local fallback."""
    now = [1_000_000.0]
    monkeypatch.setattr(rate_limit, "_wall_ms", lambda: int(now[0] * 1000))
    monkeypatch.setattr(rate_limit, "_monotonic_ms", lambda: now[0] * 1000)
    return now


@pytest.fixture
def upstash(monkeypatch: pytest.MonkeyPatch, clock: list[float]) -> FakeUpstash:
    fake = FakeUpstash()
    monkeypatch.setattr(
        rate_limit,
        "get_settings",
        lambda: limiter_settings(REDIS_URL="https://r.test/", REDIS_TOKEN="tok"),
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    monkeypatch.setattr(rate_limit, "_http_client", lambda: client)
    return fake
