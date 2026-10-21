"""
The sliding-window script against a real Redis, through Upstash's REST protocol.
Skipped unless UPSTASH_TEST_URL is set. Locally, with Docker:

    docker network create rl-test
    docker run -d --rm --name rl-redis --network rl-test redis:7-alpine
    docker run -d --rm --name rl-srh --network rl-test -p 18079:80 -e SRH_MODE=env \\
      -e SRH_TOKEN=local-test-token -e SRH_CONNECTION_STRING=redis://rl-redis:6379 \\
      hiett/serverless-redis-http:latest
    UPSTASH_TEST_URL=http://localhost:18079 UPSTASH_TEST_TOKEN=local-test-token \\
      uv run pytest tests/test_rate_limit_redis.py

Never point these at the production Upstash database.
"""

import asyncio
import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from tests.conftest import limiter_settings

from app.helpers import rate_limit

URL = os.getenv("UPSTASH_TEST_URL")
TOKEN = os.getenv("UPSTASH_TEST_TOKEN", "")

pytestmark = pytest.mark.skipif(not URL, reason="UPSTASH_TEST_URL not set")


@pytest.fixture(autouse=True)
def _real_redis(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure(monkeypatch)
    yield
    # The in-process fallback would pass most of these too; make sure it never ran.
    assert not rate_limit._local, "fell back to the local limiter: is Redis reachable?"


def configure(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    settings = limiter_settings(REDIS_URL=URL, REDIS_TOKEN=TOKEN, **overrides)
    monkeypatch.setattr(rate_limit, "get_settings", lambda: settings)


def _subject() -> str:
    return f"it-{uuid.uuid4()}"  # fresh keys, so runs never see each other's counts


async def test_allows_up_to_the_limit_then_reports_the_wait() -> None:
    subject = _subject()
    results = [await rate_limit.hit("register_ip", subject) for _ in range(4)]

    assert results[:3] == [0, 0, 0]
    assert 3590 <= results[3] <= 3600


async def test_concurrent_requests_across_connections_never_exceed_the_limit() -> None:
    subject = _subject()
    results = await asyncio.gather(*(rate_limit.hit("login_ip", subject) for _ in range(60)))

    assert sum(1 for retry in results if retry == 0) == 20


async def test_counts_expire_with_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch, RATE_LIMITS={"login_ip": "2/1"})
    subject = _subject()
    assert [await rate_limit.hit("login_ip", subject) for _ in range(3)] == [0, 0, 1]

    await asyncio.sleep(1.1)

    assert await rate_limit.hit("login_ip", subject) == 0


async def test_keys_expire_so_redis_does_not_fill_up(monkeypatch: pytest.MonkeyPatch) -> None:
    subject = _subject()
    await rate_limit.hit("api_user", subject)

    ttl_ms = await rate_limit._redis(["PTTL", rate_limit._key("api_user", subject)])

    assert isinstance(ttl_ms, int) and 0 < ttl_ms <= 60_000


async def test_reset_clears_the_count() -> None:
    subject = _subject()
    for _ in range(5):
        await rate_limit.hit("login_account", subject)
    assert await rate_limit.hit("login_account", subject) > 0

    await rate_limit.reset("login_account", subject)

    assert await rate_limit.hit("login_account", subject) == 0


async def test_old_fixed_window_keys_do_not_collide() -> None:
    # The previous limiter kept plain string counters under rl:...; ours live under rl2:.
    subject = _subject()
    await rate_limit._redis(["SET", f"rl:{rate_limit._key('login_ip', subject)[4:]}", "7"])

    assert await rate_limit.hit("login_ip", subject) == 0
