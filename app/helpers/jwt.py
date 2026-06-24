from fastapi import APIRouter, HTTPException
from jose import jwt, JWTError
from app.core.config import get_settings
from fastapi import HTTPException
from supabase import Client

config = get_settings()


router = APIRouter()
def verify_jwt(token: str):
    try:
        payload = jwt.decode(
            token,
            config.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
def get_user_from_token(supabase: Client, token: str):
    res = supabase.auth.get_user(token)

    if not res.user:
        raise HTTPException(status_code=401, detail="Invalid token")

    return res.user