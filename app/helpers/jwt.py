from fastapi import APIRouter, HTTPException
from jose import JWTError, jwt
from supabase import Client

from app.core.config import get_settings
from app.db.models.users import User

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


def get_user_from_token(supabase: Client, token: str) -> User:
    res = supabase.auth.get_user(token)

    if not res.user:
        raise HTTPException(status_code=401, detail="Invalid token")

    return res.user
