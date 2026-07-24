from fastapi import APIRouter, HTTPException
from gotrue import User as SupabaseUser
from jose import JWTError, jwt
from supabase import Client

from app.core.config import get_settings

config = get_settings()


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
    res = supabase.auth.get_user(token)
    user = res.user

    if user is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    return user
