from fastapi import APIRouter

from .routes import auth, tenants, members, billing, health

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
# api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
# api_router.include_router(tenants.router, prefix="/tenants", tags=["tenants"])
# api_router.include_router(members.router, prefix="/members", tags=["members"])
# api_router.include_router(billing.router, prefix="/billing", tags=["billing"])