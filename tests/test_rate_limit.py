import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from app.helpers import rate_limit


def _configure(
    monkeypatch: pytest.MonkeyPatch, handler: Any, url: str | None = "https://r.test"
) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        rate_limit, "get_settings", lambda: SimpleNamespace(REDIS_URL=url, REDIS_TOKEN="tok")
    )
    real = httpx.AsyncClient
    monkeypatch.setattr(
        rate_limit.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(record), **kw),
    )
    return seen


def _count(n: int) -> Any:
    return lambda request: httpx.Response(200, json=[{"result": n}, {"result": 1}])


@pytest.mark.asyncio
async def test_counts_in_upstash_with_a_hashed_key(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _configure(monkeypatch, _count(1))

    assert await rate_limit.hit("verify:email", "Ivan@Example.com", limit=5, window_seconds=60)

    (request,) = seen
    assert str(request.url) == "https://r.test/pipeline"
    assert request.headers["authorization"] == "Bearer tok"
    commands = json.loads(request.content)
    assert commands[0][0] == "INCR" and "ivan" not in commands[0][1].lower()
    assert commands[1] == ["EXPIRE", commands[0][1], "60", "NX"]


@pytest.mark.asyncio
async def test_over_the_limit_is_a_429(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, _count(6))

    with pytest.raises(HTTPException) as error:
        await rate_limit.enforce("x", "y", limit=5, window_seconds=60)
    assert error.value.status_code == 429


@pytest.mark.asyncio
async def test_redis_outage_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, lambda request: httpx.Response(500))

    assert await rate_limit.hit("x", "y", limit=1, window_seconds=60)


@pytest.mark.asyncio
async def test_unconfigured_redis_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _configure(monkeypatch, _count(99), url=None)

    assert await rate_limit.hit("x", "y", limit=1, window_seconds=60)
    assert seen == []
