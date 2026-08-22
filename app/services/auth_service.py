"""
Authentication orchestration for Supabase Auth and local profiles.

Login returns the Supabase session tokens consumed by the existing protected
routes. Registration and refresh retain the service's legacy local JWT flow.
"""

import logging
import uuid

from fastapi import HTTPException, status
from fastapi.security import HTTPBearer
from gotrue.errors import AuthApiError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from supabase import Client

from app.core.config import get_settings
from app.core.security import create_access_token, create_refresh_token
from app.repositories.profile_repo import ProfileRepository

from ..db.models.auth import TokenResponse

security = HTTPBearer()

config = get_settings()
log = logging.getLogger(__name__)


class AuthService:
    def __init__(self, supabase: Client, db: AsyncSession) -> None:
        self.supabase = supabase
        self.repo = ProfileRepository(db)

    # ── Register ──────────────────────────────────────────────────────────────

    async def register(self, email: str, password: str, full_name: str) -> TokenResponse:
        try:
            res = self.supabase.auth.sign_up(
                {
                    "email": email,
                    "password": password,
                    "options": {"data": {"full_name": full_name}},
                }
            )
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        if res.user is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Registration failed — email may already exist.",
            )

        await self.repo.upsert(
            user_id=uuid.UUID(res.user.id),
            email=email,
            full_name=full_name,
        )

        return self._build_tokens(res.user.id, email)

    # ── Login ─────────────────────────────────────────────────────────────────

    async def login(self, email: str, password: str) -> TokenResponse:
        try:
            res = await run_in_threadpool(
                self.supabase.auth.sign_in_with_password,
                {"email": email, "password": password},
            )
        except AuthApiError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        except Exception as exc:
            log.exception("Supabase login failed")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service unavailable",
            ) from exc

        if res.user is None or res.session is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return TokenResponse(
            access_token=res.session.access_token,
            refresh_token=res.session.refresh_token,
            token_type=res.session.token_type,
        )

    # ── Refresh ───────────────────────────────────────────────────────────────

    async def refresh(self, user_id: str, email: str) -> TokenResponse:
        return self._build_tokens(user_id, email)

    # ── Password reset ────────────────────────────────────────────────────────

    async def request_password_reset(self, email: str) -> None:
        try:
            self.supabase.auth.reset_password_email(email)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_tokens(self, user_id: str, email: str) -> TokenResponse:
        return TokenResponse(
            access_token=create_access_token(subject=user_id, extra_claims={"email": email}),
            refresh_token=create_refresh_token(subject=user_id),
            token_type=config.TOKEN_TYPE,
        )
