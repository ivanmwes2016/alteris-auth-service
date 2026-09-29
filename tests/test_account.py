from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException, status
from gotrue.errors import AuthApiError
from pydantic import ValidationError

from app.db.models.auth import EmailRequest, RegisterRequest
from app.services import account
from app.services.email import EmailError

IP = "203.0.113.9"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        account, "get_settings", lambda: SimpleNamespace(FRONTEND_URL="https://app.test/")
    )
    monkeypatch.setattr(account.rate_limit, "enforce", AsyncMock())


def _db(state: bool | None) -> Mock:
    """state: None = no account, False = unconfirmed, True = confirmed."""
    result = Mock()
    result.first.return_value = None if state is None else (state,)
    db = Mock()
    db.execute = AsyncMock(return_value=result)
    return db


def _supabase(error: Exception | None = None) -> Mock:
    supabase = Mock()
    supabase.auth.admin.generate_link = Mock(
        side_effect=error,
        return_value=SimpleNamespace(properties=SimpleNamespace(hashed_token="hash/123")),
    )
    return supabase


def _register(**overrides: Any) -> RegisterRequest:
    values: dict[str, Any] = {
        "first_name": " Ivan ",
        "last_name": "Mwesigwa",
        "email": "Ivan@Example.com",
        "password": "correct horse",
    }
    values.update(overrides)
    return RegisterRequest(**values)


def _link(sent: AsyncMock) -> dict[str, list[str]]:
    url = sent.await_args.kwargs["text_body"].split(": https://")[1].split("\n")[0]
    parsed = urlparse("https://" + url)
    assert parsed.path == "/auth/confirm"
    return parse_qs(parsed.query)


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------
def test_register_normalises_email_and_names() -> None:
    payload = _register()
    assert payload.email == "ivan@example.com"
    assert payload.first_name == "Ivan"


@pytest.mark.parametrize("bad", ["https://evil.test", "//evil.test", "evil", "/\\evil"])
def test_next_must_be_a_path_on_this_site(bad: str) -> None:
    with pytest.raises(ValidationError):
        _register(next=bad)


@pytest.mark.parametrize("password", ["short", "x" * 73])
def test_password_length_is_enforced(password: str) -> None:
    with pytest.raises(ValidationError):
        _register(password=password)


def test_invalid_email_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EmailRequest(email="not-an-email")


# ---------------------------------------------------------------------------
# Signup
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_new_signup_creates_the_account_and_emails_our_confirm_link() -> None:
    supabase, sent = _supabase(), AsyncMock()
    with patch.object(account, "send_email", sent):
        message = await account.register(
            _db(None), supabase, _register(next="/accept-invite?token=t"), IP
        )

    params = supabase.auth.admin.generate_link.call_args.args[0]
    assert params["type"] == "signup"
    assert params["email"] == "ivan@example.com"
    assert params["options"]["data"] == {"first_name": "Ivan", "last_name": "Mwesigwa"}
    link = _link(sent)
    assert link == {
        "type": ["signup"],
        "token_hash": ["hash/123"],
        "next": ["/accept-invite?token=t"],
    }
    assert sent.await_args.kwargs["to"] == "ivan@example.com"
    assert message == account.CHECK_EMAIL


@pytest.mark.asyncio
async def test_signup_with_a_confirmed_email_sends_account_exists_and_same_answer() -> None:
    supabase, sent = _supabase(), AsyncMock()
    with patch.object(account, "send_email", sent):
        message = await account.register(_db(True), supabase, _register(), IP)

    supabase.auth.admin.generate_link.assert_not_called()
    assert "already have an account" in sent.await_args.kwargs["subject"]
    assert message == account.CHECK_EMAIL


@pytest.mark.asyncio
async def test_unconfirmed_signup_resends_confirmation_keeping_password() -> None:
    supabase, sent = _supabase(), AsyncMock()
    with patch.object(account, "send_email", sent):
        message = await account.register(_db(False), supabase, _register(), IP)

    params = supabase.auth.admin.generate_link.call_args.args[0]
    assert params == {"type": "magiclink", "email": "ivan@example.com"}
    assert _link(sent)["type"] == ["magiclink"]
    assert message == account.CHECK_EMAIL


@pytest.mark.asyncio
async def test_signup_race_with_existing_email_still_gives_the_same_answer() -> None:
    supabase, sent = _supabase(AuthApiError("exists", 422, "email_exists")), AsyncMock()
    with patch.object(account, "send_email", sent):
        message = await account.register(_db(None), supabase, _register(), IP)

    assert "already have an account" in sent.await_args.kwargs["subject"]
    assert message == account.CHECK_EMAIL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (AuthApiError("pwned", 422, "weak_password"), status.HTTP_422_UNPROCESSABLE_ENTITY),
        (AuthApiError("raw supabase text", 400, "validation_failed"), status.HTTP_400_BAD_REQUEST),
        (AuthApiError("down", 500, "unexpected_failure"), status.HTTP_503_SERVICE_UNAVAILABLE),
    ],
)
async def test_supabase_errors_are_mapped_without_leaking_their_text(
    error: AuthApiError, code: int
) -> None:
    with (
        patch.object(account, "send_email", AsyncMock()),
        pytest.raises(HTTPException) as raised,
    ):
        await account.register(_db(None), _supabase(error), _register(), IP)

    assert raised.value.status_code == code
    assert "raw supabase" not in str(raised.value.detail)


@pytest.mark.asyncio
async def test_signup_email_failure_is_a_502_so_they_can_retry() -> None:
    with (
        patch.object(account, "send_email", AsyncMock(side_effect=EmailError("down"))),
        pytest.raises(HTTPException) as raised,
    ):
        await account.register(_db(None), _supabase(), _register(), IP)

    assert raised.value.status_code == status.HTTP_502_BAD_GATEWAY


@pytest.mark.asyncio
async def test_signup_is_rate_limited_per_ip_and_email() -> None:
    enforce = AsyncMock()
    with (
        patch.object(account.rate_limit, "enforce", enforce),
        patch.object(account, "send_email", AsyncMock()),
    ):
        await account.register(_db(None), _supabase(), _register(), IP)

    subjects = {call.args[1] for call in enforce.await_args_list}
    assert subjects == {IP, "ivan@example.com"}


# ---------------------------------------------------------------------------
# Resend verification / forgot password: one answer whatever the account state
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(("state", "emails"), [(None, 0), (True, 0), (False, 1)])
async def test_resend_only_emails_unconfirmed_accounts(state: bool | None, emails: int) -> None:
    sent = AsyncMock()
    with patch.object(account, "send_email", sent):
        message = await account.resend_verification(
            _db(state), _supabase(), EmailRequest(email="a@b.com"), IP
        )

    assert sent.await_count == emails
    assert message == account.MAYBE_SENT_CONFIRM


@pytest.mark.asyncio
@pytest.mark.parametrize(("state", "emails"), [(None, 0), (True, 1), (False, 1)])
async def test_forgot_password_emails_only_existing_accounts(
    state: bool | None, emails: int
) -> None:
    supabase, sent = _supabase(), AsyncMock()
    with patch.object(account, "send_email", sent):
        message = await account.forgot_password(
            _db(state), supabase, EmailRequest(email="a@b.com"), IP
        )

    assert sent.await_count == emails
    assert message == account.MAYBE_SENT_RESET
    if emails:
        assert supabase.auth.admin.generate_link.call_args.args[0]["type"] == "recovery"
        assert _link(sent)["next"] == ["/reset-password"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        {"send_email": AsyncMock(side_effect=EmailError("down"))},
        {"generate": AuthApiError("down", 500, "unexpected_failure")},
    ],
)
async def test_forgot_password_failures_do_not_reveal_the_account(failure: dict[str, Any]) -> None:
    supabase = _supabase(failure.get("generate"))
    with patch.object(account, "send_email", failure.get("send_email", AsyncMock())):
        message = await account.forgot_password(
            _db(True), supabase, EmailRequest(email="a@b.com"), IP
        )

    assert message == account.MAYBE_SENT_RESET
