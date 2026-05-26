from fastapi import APIRouter, HTTPException
from jose import jwt, JWTError
from app.core.config import get_settings

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