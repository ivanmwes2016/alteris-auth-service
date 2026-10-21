import asyncio
import json
import logging
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from tests.conftest import FakeUpstash, limiter_settings

from app.helpers import rate_limit


def _settings(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    monkeypatch.setattr(rate_limit, "get_settings", lambda: limiter_settings(**overrides))


# ── Counting ─────────────────────────────────────────────────────────────────
async def test_counts_in_upstash_with_one_atomic_script_and_a_hashed_key(
    upstash: FakeUpstash,
) -> None:
    assert await rate_limit.hit("verify_account", "Ivan@Example.com") == 0

    (request,) = upstash.requests
    assert str(request.url) == "https://r.test"
    assert request.headers["authorization"] == "Bearer tok"
    command = json.loads(request.content)
    assert command[0] == "EVAL" and command[1] == rate_limit.SLIDING_WINDOW
    assert command[3].startswith("rl2:verify_account:")
    assert "ivan" not in command[3].lower()
    assert command[5:7] == ["3600000", "3"]  # window ms, limit


async def test_same_subject_in_any_case_or_spacing_shares_a_count(upstash: FakeUpstash) -> None:
    for email in ["a@b.com", " A@B.com", "a@B.COM "]:
        await rate_limit.hit("reset_account", email)
    assert await rate_limit.hit("reset_account", "a@b.com") > 0


async def test_within_the_limit_is_allowed_and_one_more_is_a_429_with_retry_after(
    upstash: FakeUpstash, clock: list[float]
) -> None:
    for _ in range(5):
        await rate_limit.enforce("login_account", "a@b.com")
        clock[0] += 10

    with pytest.raises(HTTPException) as error:
        await rate_limit.enforce("login_account", "a@b.com")

    assert error.value.status_code == 429
    # The first attempt (50s ago) leaves the 15-minute window in 850s.
    assert error.value.headers == {"Retry-After": "850"}
    assert error.value.detail == "Too many attempts. Please try again in 15 minutes."


async def test_a_rejected_request_does_not_extend_the_wait(
    upstash: FakeUpstash, clock: list[float]
) -> None:
    for _ in range(3):
        await rate_limit.hit("register_ip", "198.51.100.7")
    first = await rate_limit.hit("register_ip", "198.51.100.7")
    clock[0] += 60
    assert await rate_limit.hit("register_ip", "198.51.100.7") == first - 60


async def test_counts_expire_as_the_window_slides(upstash: FakeUpstash, clock: list[float]) -> None:
    await rate_limit.hit("register_ip", "198.51.100.7")
    clock[0] += 1800
    await rate_limit.hit("register_ip", "198.51.100.7")
    await rate_limit.hit("register_ip", "198.51.100.7")
    assert await rate_limit.hit("register_ip", "198.51.100.7") > 0

    clock[0] += 1801  # the first request has left the window; the other two haven't
    assert await rate_limit.hit("register_ip", "198.51.100.7") == 0
    assert await rate_limit.hit("register_ip", "198.51.100.7") > 0


async def test_rules_and_subjects_are_counted_separately(upstash: FakeUpstash) -> None:
    for _ in range(3):
        await rate_limit.hit("reset_account", "a@b.com")

    assert await rate_limit.hit("reset_account", "a@b.com") > 0
    assert await rate_limit.hit("reset_account", "c@d.com") == 0
    assert await rate_limit.hit("verify_account", "a@b.com") == 0


async def test_reset_forgets_a_subject(upstash: FakeUpstash) -> None:
    for _ in range(5):
        await rate_limit.hit("login_account", "a@b.com")

    await rate_limit.reset("login_account", "a@b.com")

    assert json.loads(upstash.requests[-1].content)[0] == "DEL"
    assert await rate_limit.hit("login_account", "a@b.com") == 0


async def test_concurrent_requests_never_exceed_the_limit(upstash: FakeUpstash) -> None:
    results = await asyncio.gather(*(rate_limit.hit("login_ip", "1.2.3.4") for _ in range(50)))
    assert sum(1 for retry in results if retry == 0) == 20


# ── Configuration ────────────────────────────────────────────────────────────
async def test_limits_come_from_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, RATE_LIMITS={"login_ip": "2/30"})

    assert rate_limit.rule("login_ip") == rate_limit.Rule("login_ip", 2, 30)
    assert rate_limit.rule("register_ip") == rate_limit.Rule("register_ip", 3, 3600)


@pytest.mark.parametrize("bad", ["2", "x/60", "0/60", "5/0", "5/60/1"])
async def test_a_malformed_override_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, bad: str
) -> None:
    _settings(monkeypatch, RATE_LIMITS={"login_ip": bad})

    assert rate_limit.rule("login_ip") == rate_limit.Rule("login_ip", 20, 60)
    assert "rate_limit.bad_config rule=login_ip" in caplog.text


async def test_every_default_rule_parses() -> None:
    for name in rate_limit.DEFAULT_RULES:
        assert rate_limit.rule(name).limit >= 1


async def test_disabled_limiter_allows_everything_without_calling_redis(
    monkeypatch: pytest.MonkeyPatch, upstash: FakeUpstash
) -> None:
    _settings(
        monkeypatch, REDIS_URL="https://r.test/", REDIS_TOKEN="t", RATE_LIMITING_ENABLED=False
    )

    for _ in range(10):
        await rate_limit.enforce("register_ip", "1.2.3.4")
    assert upstash.requests == []


# ── Redis outages ────────────────────────────────────────────────────────────
async def test_redis_outage_keeps_sensitive_rules_enforced_in_process(
    upstash: FakeUpstash, caplog: pytest.LogCaptureFixture
) -> None:
    upstash.down = True

    for _ in range(3):
        await rate_limit.enforce("register_ip", "1.2.3.4")
    with pytest.raises(HTTPException) as error:
        await rate_limit.enforce("register_ip", "1.2.3.4")

    assert error.value.status_code == 429
    assert "rate_limit.redis_unavailable reason=HTTPStatusError fallback=local" in caplog.text
    assert caplog.text.count("redis_unavailable") == 1  # throttled, not one line per request


async def test_redis_outage_lets_general_api_traffic_through(upstash: FakeUpstash) -> None:
    upstash.down = True

    for _ in range(500):
        await rate_limit.enforce("api_user", "user-1", sensitive=False)


async def test_redis_timeouts_count_as_outages(
    monkeypatch: pytest.MonkeyPatch, upstash: FakeUpstash
) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(timeout))
    monkeypatch.setattr(rate_limit, "_http_client", lambda: client)

    assert await rate_limit.hit("api_user", "u", sensitive=False) == 0
    for _ in range(3):
        assert await rate_limit.hit("register_ip", "1.2.3.4") == 0
    assert await rate_limit.hit("register_ip", "1.2.3.4") > 0


async def test_unexpected_redis_replies_count_as_outages(
    monkeypatch: pytest.MonkeyPatch, upstash: FakeUpstash
) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"error": "NOSCRIPT"}))
    )
    monkeypatch.setattr(rate_limit, "_http_client", lambda: client)

    for _ in range(3):
        await rate_limit.hit("register_ip", "1.2.3.4")
    assert await rate_limit.hit("register_ip", "1.2.3.4") > 0


async def test_unconfigured_redis_uses_the_local_fallback_without_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_http() -> httpx.AsyncClient:
        raise AssertionError("no HTTP when Redis isn't configured")

    monkeypatch.setattr(rate_limit, "_http_client", no_http)

    for _ in range(5):
        await rate_limit.hit("login_account", "a@b.com")
    assert await rate_limit.hit("login_account", "a@b.com") > 0


async def test_local_fallback_windows_expire(upstash: FakeUpstash, clock: list[float]) -> None:
    upstash.down = True
    for _ in range(3):
        await rate_limit.hit("register_ip", "1.2.3.4")
    assert await rate_limit.hit("register_ip", "1.2.3.4") == 3600

    clock[0] += 3600
    assert await rate_limit.hit("register_ip", "1.2.3.4") == 0


async def test_local_fallback_memory_is_bounded(
    monkeypatch: pytest.MonkeyPatch, upstash: FakeUpstash
) -> None:
    upstash.down = True
    monkeypatch.setattr(rate_limit, "LOCAL_MAX_KEYS", 10)

    for n in range(50):
        await rate_limit.hit("register_ip", f"10.0.0.{n}")
    assert len(rate_limit._local) == 10


async def test_recovers_once_redis_is_back(upstash: FakeUpstash) -> None:
    upstash.down = True
    for _ in range(3):
        await rate_limit.hit("register_ip", "1.2.3.4")
    upstash.down = False

    # Redis never saw those requests, so it counts from zero again.
    assert await rate_limit.hit("register_ip", "1.2.3.4") == 0
    assert upstash.sets


# ── Logging ──────────────────────────────────────────────────────────────────
async def test_blocks_are_logged_once_a_minute_with_ips_but_never_emails(
    upstash: FakeUpstash, clock: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=rate_limit.__name__)
    for _ in range(5):
        await rate_limit.hit("login_account", "victim@school.org")
    for _ in range(20):
        await rate_limit.hit("login_ip", "203.0.113.5")

    for _ in range(3):
        with pytest.raises(HTTPException):
            await rate_limit.enforce("login_account", "victim@school.org")
        with pytest.raises(HTTPException):
            await rate_limit.enforce("login_ip", "203.0.113.5")

    blocked = [r.getMessage() for r in caplog.records if "rate_limit.blocked" in r.getMessage()]
    assert len(blocked) == 2
    assert "rule=login_ip subject=203.0.113.5" in caplog.text
    assert "victim" not in caplog.text and "school.org" not in caplog.text


# ── Client IP ────────────────────────────────────────────────────────────────
def _request(headers: dict[str, str], peer: str = "10.1.2.3") -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (peer, 1234),
        }
    )


def test_cloudflare_header_wins_over_anything_the_client_sends() -> None:
    request = _request(
        {"cf-connecting-ip": "198.51.100.20", "x-forwarded-for": "1.1.1.1, 198.51.100.20"}
    )
    assert rate_limit.client_ip(request) == "198.51.100.20"


def test_spoofed_forwarded_for_entries_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, CLIENT_IP_HEADER=None, TRUSTED_PROXY_HOPS=1)

    # The client sent "6.6.6.6, 7.7.7.7"; our proxy appended the address it saw.
    request = _request({"x-forwarded-for": "6.6.6.6, 7.7.7.7, 198.51.100.20"})
    assert rate_limit.client_ip(request) == "198.51.100.20"


def test_trusted_hops_count_from_the_right(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, CLIENT_IP_HEADER=None, TRUSTED_PROXY_HOPS=2)

    request = _request({"x-forwarded-for": "6.6.6.6, 198.51.100.20, 10.0.0.1"})
    assert rate_limit.client_ip(request) == "198.51.100.20"


def test_too_few_forwarded_entries_falls_back_to_the_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, CLIENT_IP_HEADER=None, TRUSTED_PROXY_HOPS=2)

    assert rate_limit.client_ip(_request({"x-forwarded-for": "6.6.6.6"})) == "10.1.2.3"


def test_garbage_in_ip_headers_is_not_used_as_a_key() -> None:
    request = _request({"cf-connecting-ip": "not-an-ip", "x-forwarded-for": "x"})
    assert rate_limit.client_ip(request) == "10.1.2.3"


def test_ipv6_clients_are_grouped_by_their_64_block() -> None:
    a = rate_limit.client_ip(_request({"cf-connecting-ip": "2001:db8:1:2::aaaa"}))
    b = rate_limit.client_ip(_request({"cf-connecting-ip": "2001:db8:1:2:ffff::1"}))
    assert a == b == "2001:db8:1:2::/64"


def test_ip_source_is_logged_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(rate_limit, "_ip_source_logged", False)
    for _ in range(3):
        rate_limit.client_ip(_request({"cf-connecting-ip": "198.51.100.20"}))

    assert caplog.text.count("rate_limit.ip_source source=cf-connecting-ip") == 1
