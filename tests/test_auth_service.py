import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException, status
from gotrue.errors import AuthApiError
from sqlalchemy.exc import IntegrityError

from app.api.v1.routes import auth
from app.services.auth_service import AuthService


def build_service(auth_response: object) -> AuthService:
    supabase = Mock()
    supabase.auth.sign_in_with_password.return_value = auth_response
    return AuthService(supabase=supabase, db=Mock())


@pytest.mark.asyncio
async def test_login_returns_supabase_session_tokens() -> None:
    response = SimpleNamespace(
        user=SimpleNamespace(id="user-id", email="user@example.com"),
        session=SimpleNamespace(
            access_token="access-token",
            refresh_token="refresh-token",
            token_type="bearer",
        ),
    )

    result = await build_service(response).login("user@example.com", "password")

    assert result.access_token == "access-token"
    assert result.refresh_token == "refresh-token"
    assert result.token_type == "bearer"


@pytest.mark.asyncio
async def test_login_rejects_invalid_credentials_without_leaking_provider_details() -> None:
    service = build_service(SimpleNamespace(user=None, session=None))
    service.supabase.auth.sign_in_with_password.side_effect = AuthApiError(
        "provider-specific error",
        status.HTTP_400_BAD_REQUEST,
        "invalid_credentials",
    )

    with pytest.raises(HTTPException) as error:
        await service.login("user@example.com", "wrong-password")

    assert error.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert error.value.detail == "Invalid email or password"


@pytest.mark.asyncio
async def test_login_reports_provider_outage() -> None:
    service = build_service(SimpleNamespace(user=None, session=None))
    service.supabase.auth.sign_in_with_password.side_effect = RuntimeError("network unavailable")

    with pytest.raises(HTTPException) as error:
        await service.login("user@example.com", "password")

    assert error.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert error.value.detail == "Authentication service unavailable"


@pytest.mark.asyncio
async def test_new_login_whose_email_is_held_by_an_old_users_row_gets_409_not_500() -> None:
    supabase_user = SimpleNamespace(id=str(uuid.uuid4()), email="a@b.com")
    db = Mock()
    db.get = AsyncMock(return_value=None)
    db.scalar = AsyncMock(return_value=None)
    db.flush = AsyncMock(side_effect=IntegrityError("insert", {}, Exception("ix_users_email")))
    db.rollback = AsyncMock()

    with pytest.raises(HTTPException) as error:
        await auth.get_current_user(Mock(), supabase_user, db, Mock())

    assert error.value.status_code == status.HTTP_409_CONFLICT
    db.rollback.assert_awaited_once()


def _new_login_db(old_id: uuid.UUID | None) -> Mock:
    db = Mock()
    db.get = AsyncMock(return_value=None)
    db.scalar = AsyncMock(return_value=old_id)
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_new_login_clears_a_leftover_row_holding_its_email() -> None:
    supabase_user = SimpleNamespace(id=str(uuid.uuid4()), email="a@b.com")
    old_id = uuid.uuid4()
    db = _new_login_db(old_id)
    cleanup = AsyncMock(return_value=True)

    with patch.object(auth, "remove_if_stale", cleanup):
        user = await auth.get_current_user(Mock(), supabase_user, db, Mock())

    cleanup.assert_awaited_once()
    assert cleanup.await_args.args[2] == old_id
    assert user.id == supabase_user.id


@pytest.mark.asyncio
async def test_new_login_with_no_email_clash_skips_the_cleanup() -> None:
    supabase_user = SimpleNamespace(id=str(uuid.uuid4()), email="a@b.com")
    cleanup = AsyncMock()

    with patch.object(auth, "remove_if_stale", cleanup):
        await auth.get_current_user(Mock(), supabase_user, _new_login_db(None), Mock())

    cleanup.assert_not_awaited()


@pytest.mark.parametrize(
    ("metadata", "stored", "expected"),
    [
        ({"first_name": "Ivan", "last_name": "Mwesigwa"}, None, "Ivan Mwesigwa"),
        ({"first_name": " Ivan ", "last_name": ""}, "Typed By Admin", "Ivan"),
        ({}, "Ada Lovelace", "Ada Lovelace"),
        ({"full_name": "Old Style"}, None, "Old Style"),
        ({"name": "Older"}, "  ", "Older"),
        ({}, None, None),
        (None, None, None),
    ],
)
def test_display_name_prefers_signup_first_and_last_name(
    metadata: dict[str, str] | None, stored: str | None, expected: str | None
) -> None:
    assert auth.display_name(metadata, stored) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (
            AuthApiError("Email not confirmed", 400, "email_not_confirmed"),
            status.HTTP_403_FORBIDDEN,
        ),
        (
            AuthApiError("slow down", 429, "over_request_rate_limit"),
            status.HTTP_429_TOO_MANY_REQUESTS,
        ),
        (AuthApiError("boom", 500, "unexpected_failure"), status.HTTP_503_SERVICE_UNAVAILABLE),
    ],
)
async def test_login_maps_supabase_states_to_clear_statuses(error: AuthApiError, code: int) -> None:
    service = build_service(SimpleNamespace(user=None, session=None))
    service.supabase.auth.sign_in_with_password.side_effect = error

    with pytest.raises(HTTPException) as raised:
        await service.login("user@example.com", "password")

    assert raised.value.status_code == code
