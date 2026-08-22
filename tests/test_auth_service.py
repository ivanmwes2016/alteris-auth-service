from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, status
from gotrue.errors import AuthApiError

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
