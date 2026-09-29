"""
Password login through Supabase Auth.

Returns Supabase's own session tokens; the service never issues tokens of its own.
Signup, verification and password reset live in app/services/account.py.
"""

import logging

from fastapi import HTTPException, status
from gotrue.errors import AuthApiError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from supabase import Client

from ..db.models.auth import TokenResponse

log = logging.getLogger(__name__)


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password",
        headers={"WWW-Authenticate": "Bearer"},
    )


class AuthService:
    def __init__(self, supabase: Client, db: AsyncSession) -> None:
        self.supabase = supabase
        self.db = db

    async def login(self, email: str, password: str) -> TokenResponse:
        try:
            res = await run_in_threadpool(
                self.supabase.auth.sign_in_with_password,
                {"email": email, "password": password},
            )
        except AuthApiError as exc:
            # Only reachable with the right password, so it reveals nothing new.
            if exc.code == "email_not_confirmed":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Email verification required.",
                ) from exc
            if exc.status == status.HTTP_429_TOO_MANY_REQUESTS:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many attempts. Please wait a while and try again.",
                ) from exc
            if exc.status >= 500:
                log.error("Supabase login failed: status=%s code=%s", exc.status, exc.code)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Authentication service unavailable",
                ) from exc
            raise _invalid_credentials() from exc
        except Exception as exc:
            log.exception("Supabase login failed")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service unavailable",
            ) from exc

        if res.user is None or res.session is None:
            raise _invalid_credentials()

        return TokenResponse(
            access_token=res.session.access_token,
            refresh_token=res.session.refresh_token,
            token_type=res.session.token_type,
        )
