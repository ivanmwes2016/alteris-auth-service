from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter()


@router.get("/")
def root() -> dict[str, str]:
    return {"status": "ok", "service": get_settings().APP_NAME}


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}
