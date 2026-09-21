import logging

from fastapi import APIRouter, HTTPException
from gotrue import User as SupabaseUser
from gotrue.errors import AuthApiError
from jose import JWTError, jwt
from supabase import Client

from app.core.config import get_settings

config = get_settings()
log = logging.getLogger(__name__)


router = APIRouter()


def verify_jwt(token: str) -> dict[str, str]:
    try:
        payload = jwt.decode(
            token,
            config.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload
    except JWTError as e:
        raise HTTPException(status_code=401, detail="Invalid token") from e


def get_user_from_token(supabase: Client, token: str) -> SupabaseUser:
    try:
        res = supabase.auth.get_user(token)
    except AuthApiError as exc:
        # Log why (never the token) — the client only ever sees a bare 401.
        log.warning(
            "Supabase rejected an access token: status=%s code=%s message=%s",
            exc.status,
            exc.code,
            exc.message,
        )
        # Supabase rejects expired/invalid tokens, and tokens for users that have since been
        # deleted. That's the client's problem (401), unless Supabase itself is failing (503) —
        # which mustn't look like a bad token or clients would sign everyone out in an outage.
        if exc.status >= 500:
            raise HTTPException(status_code=503, detail="Authentication unavailable") from exc
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    if res is None or res.user is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    return res.user
