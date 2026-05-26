"""
Auth service — wraps Supabase Auth (GoTrue) + ProfileRepository.

Supabase handles:  password hashing, email confirmation, OAuth.
We handle:         our own JWT layer + profile persistence via SQLAlchemy.
"""
import uuid

from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import Client

from app.core.security import create_access_token, create_refresh_token
from app.repositories.profile_repo import ProfileRepository
from ..db.models.auth import TokenResponse
from app.core.config import get_settings
from jose import jwt, JWTError


security = HTTPBearer()

config = get_settings()


class AuthService:
    def __init__(self, supabase: Client, db: AsyncSession):
        self.supabase = supabase
        self.repo = ProfileRepository(db)

    # ── Register ──────────────────────────────────────────────────────────────

    async def register(self, email: str, password: str, full_name: str) -> TokenResponse:
        try:
            res = self.supabase.auth.sign_up(
                {"email": email, "password": password, "options": {"data": {"full_name": full_name}}}
            )
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

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
            res = self.supabase.auth.sign_in_with_password({"email": email, "password": password})
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

        if res.user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

        return self._build_tokens(res.user.id, res.user.email or email)

    # ── Refresh ───────────────────────────────────────────────────────────────

    async def refresh(self, user_id: str, email: str) -> TokenResponse:
        return self._build_tokens(user_id, email)

    # ── Password reset ────────────────────────────────────────────────────────

    async def request_password_reset(self, email: str) -> None:
        try:
            self.supabase.auth.reset_password_email(email)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_tokens(self, user_id: str, email: str) -> TokenResponse:
        return TokenResponse(
            access_token=create_access_token(subject=user_id, extra_claims={"email": email}),
            refresh_token=create_refresh_token(subject=user_id),
        )