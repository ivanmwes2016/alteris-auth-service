import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import HTTPException

from app.db.models.auth import EmailRequest, RegisterRequest
from app.helpers import captcha
from app.services import account

TOKEN = "XXXX.DUMMY.TOKEN.XXXX"


def _enforce(monkeypatch: pytest.MonkeyPatch, secret: str = "s3cret") -> None:
    monkeypatch.setattr(
        captcha, "get_settings", lambda: SimpleNamespace(TURNSTILE_SECRET_KEY=secret)
    )


def _cloudflare(monkeypatch: pytest.MonkeyPatch, *replies: Any) -> list[httpx.Request]:
    """Siteverify answering with `replies` in turn: a JSON body, a status code or an error."""
    seen: list[httpx.Request] = []
    queue = list(replies)

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, int):
            return httpx.Response(reply)
        return httpx.Response(200, json=reply)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        captcha.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handle), **kw),
    )
    return seen


def _form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


async def _status(token: str | None) -> int:
    try:
        await captcha.verify(token, form="register", ip="203.0.113.5")
    except HTTPException as exc:
        return exc.status_code
    return 200


# ── Switched off ─────────────────────────────────────────────────────────────
async def test_without_a_secret_nothing_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _cloudflare(monkeypatch)
    assert await _status(None) == 200
    assert seen == []


# ── Verifying ────────────────────────────────────────────────────────────────
async def test_a_valid_token_passes_after_checking_it_with_cloudflare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enforce(monkeypatch)
    seen = _cloudflare(monkeypatch, {"success": True})

    assert await _status(TOKEN) == 200

    (request,) = seen
    assert str(request.url) == captcha.SITEVERIFY_URL
    sent = _form(request)
    assert sent["secret"] == "s3cret" and sent["response"] == TOKEN
    assert sent["idempotency_key"]


@pytest.mark.parametrize("token", [None, "", "x" * 2049])
async def test_missing_or_oversized_tokens_are_refused_without_asking_cloudflare(
    monkeypatch: pytest.MonkeyPatch, token: str | None
) -> None:
    _enforce(monkeypatch)
    seen = _cloudflare(monkeypatch)

    assert await _status(token) == 400
    assert seen == []


@pytest.mark.parametrize(
    "codes", [["invalid-input-response"], ["timeout-or-duplicate"], ["bad-request"], []]
)
async def test_rejected_tokens_are_a_400_with_a_friendly_message(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, codes: list[str]
) -> None:
    _enforce(monkeypatch)
    _cloudflare(monkeypatch, {"success": False, "error-codes": codes})

    with pytest.raises(HTTPException) as error:
        await captcha.verify(TOKEN, form="register", ip="203.0.113.5")

    assert error.value.status_code == 400
    assert error.value.detail == captcha.FAILED
    assert "captcha.failed form=register ip=203.0.113.5" in caplog.text
    assert TOKEN not in caplog.text and "s3cret" not in caplog.text


async def test_a_bad_secret_is_our_fault_not_the_visitors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _enforce(monkeypatch)
    _cloudflare(monkeypatch, {"success": False, "error-codes": ["invalid-input-secret"]})

    assert await _status(TOKEN) == 503
    assert "captcha.misconfigured" in caplog.text


# ── Cloudflare trouble ───────────────────────────────────────────────────────
async def test_one_retry_with_the_same_idempotency_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _enforce(monkeypatch)
    seen = _cloudflare(monkeypatch, httpx.ConnectTimeout("slow"), {"success": True})

    assert await _status(TOKEN) == 200
    assert len(seen) == 2
    assert _form(seen[0])["idempotency_key"] == _form(seen[1])["idempotency_key"]


@pytest.mark.parametrize(
    "replies",
    [
        (500, 502),
        (httpx.ConnectError("down"), httpx.ReadTimeout("slow")),
        ({"success": False, "error-codes": ["internal-error"]},) * 2,
        ("not json-object", "still not"),
    ],
)
async def test_an_unreachable_cloudflare_refuses_the_request(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, replies: tuple[Any, ...]
) -> None:
    _enforce(monkeypatch)
    _cloudflare(monkeypatch, *replies)

    with pytest.raises(HTTPException) as error:
        await captcha.verify(TOKEN, form="register", ip="203.0.113.5")

    assert error.value.status_code == 503
    assert error.value.detail == captcha.UNAVAILABLE
    assert "captcha.unavailable" in caplog.text


# ── The forms that use it ────────────────────────────────────────────────────
def _db() -> Mock:
    result = Mock()
    result.first.return_value = None
    db = Mock()
    db.execute = AsyncMock(return_value=result)
    db.scalar = AsyncMock(return_value=None)
    return db


@pytest.fixture
def no_email(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    deliver = AsyncMock()
    monkeypatch.setattr(account, "_deliver", deliver)
    monkeypatch.setattr(
        account, "get_settings", lambda: SimpleNamespace(FRONTEND_URL="https://app.test")
    )
    return deliver


def _register(token: str | None) -> RegisterRequest:
    return RegisterRequest(
        first_name="A",
        last_name="B",
        email="a@b.com",
        password="correct horse",
        captcha_token=token,
    )


async def test_signup_without_a_token_creates_nothing_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, no_email: AsyncMock
) -> None:
    _enforce(monkeypatch)
    supabase = Mock()

    with pytest.raises(HTTPException) as error:
        await account.register(_db(), supabase, _register(None), "203.0.113.5")

    assert error.value.status_code == 400
    supabase.auth.admin.generate_link.assert_not_called()
    no_email.assert_not_awaited()


async def test_bot_requests_do_not_use_up_a_shared_networks_signup_limit(
    monkeypatch: pytest.MonkeyPatch, no_email: AsyncMock
) -> None:
    _enforce(monkeypatch)
    for _ in range(10):
        with pytest.raises(HTTPException):
            await account.register(_db(), Mock(), _register(None), "203.0.113.5")

    _cloudflare(monkeypatch, {"success": True})
    supabase = Mock()
    supabase.auth.admin.generate_link.return_value = SimpleNamespace(
        properties=SimpleNamespace(hashed_token="h")
    )
    await account.register(_db(), supabase, _register(TOKEN), "203.0.113.5")
    no_email.assert_awaited_once()


@pytest.mark.parametrize("flow", [account.resend_verification, account.forgot_password])
async def test_account_email_forms_need_a_token_too(
    monkeypatch: pytest.MonkeyPatch, no_email: AsyncMock, flow: Any
) -> None:
    _enforce(monkeypatch)
    with pytest.raises(HTTPException) as error:
        await flow(_db(), Mock(), EmailRequest(email="a@b.com"), "203.0.113.5")
    assert error.value.status_code == 400


def test_the_token_field_is_optional_and_bounded() -> None:
    assert EmailRequest(email="a@b.com").captcha_token is None
    with pytest.raises(ValueError):
        EmailRequest(email="a@b.com", captcha_token="x" * 2049)


# ── Against Cloudflare itself (opt-in) ───────────────────────────────────────
live = pytest.mark.skipif(
    not os.getenv("TURNSTILE_LIVE_TEST"), reason="set TURNSTILE_LIVE_TEST=1 to call Cloudflare"
)


@live
@pytest.mark.parametrize(
    ("secret", "expected"),
    [
        ("1x0000000000000000000000000000000AA", 200),  # Cloudflare's always-pass test key
        ("2x0000000000000000000000000000000AA", 400),  # always fails
        ("3x0000000000000000000000000000000AA", 400),  # token already spent
    ],
)
async def test_cloudflare_test_keys(
    monkeypatch: pytest.MonkeyPatch, secret: str, expected: int
) -> None:
    _enforce(monkeypatch, secret)
    assert await _status(TOKEN) == expected
