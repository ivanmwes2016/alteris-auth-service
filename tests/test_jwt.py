from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from gotrue.errors import AuthApiError

from app.helpers.jwt import get_user_from_token


def _supabase(*, user: object = None, error: Exception | None = None) -> Mock:
    supabase = Mock()
    supabase.auth.get_user = Mock(return_value=SimpleNamespace(user=user), side_effect=error)
    return supabase


def test_valid_token_returns_the_supabase_user() -> None:
    user = SimpleNamespace(id="u1")

    assert get_user_from_token(_supabase(user=user), "tok") is user  # type: ignore[arg-type]


def test_token_with_no_user_is_unauthorized() -> None:
    with pytest.raises(HTTPException) as error:
        get_user_from_token(_supabase(user=None), "tok")  # type: ignore[arg-type]

    assert error.value.status_code == 401


@pytest.mark.parametrize(
    ("status", "message", "code"),
    [
        (403, "User from sub claim in JWT does not exist", "user_not_found"),
        (401, "invalid JWT: token is expired", "bad_jwt"),
        (400, "invalid JWT", "bad_jwt"),
    ],
)
def test_tokens_supabase_rejects_are_401_not_a_crash(status: int, message: str, code: str) -> None:
    supabase = _supabase(error=AuthApiError(message, status, code))

    with pytest.raises(HTTPException) as error:
        get_user_from_token(supabase, "tok")  # type: ignore[arg-type]

    assert error.value.status_code == 401
    assert error.value.detail == "Invalid token"


@pytest.mark.parametrize("status", [500, 502, 503])
def test_a_supabase_outage_is_503_so_clients_are_not_signed_out(status: int) -> None:
    supabase = _supabase(error=AuthApiError("upstream down", status, "unexpected_failure"))

    with pytest.raises(HTTPException) as error:
        get_user_from_token(supabase, "tok")  # type: ignore[arg-type]

    assert error.value.status_code == 503


def test_the_reason_is_logged_but_never_the_token(caplog: pytest.LogCaptureFixture) -> None:
    supabase = _supabase(error=AuthApiError("token is expired", 401, "bad_jwt"))

    with caplog.at_level("WARNING"), pytest.raises(HTTPException):
        get_user_from_token(supabase, "super-secret-token")  # type: ignore[arg-type]

    assert "bad_jwt" in caplog.text
    assert "token is expired" in caplog.text
    assert "super-secret-token" not in caplog.text
