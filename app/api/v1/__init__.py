from fastapi import APIRouter

from app.api.v1.routes import stripe_webhooks

from .routes import auth, billing, enquiries, health, tenant_profile, workspaces

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(workspaces.router, prefix="/workspaces", tags=["workspaces"])
api_router.include_router(stripe_webhooks.router, prefix="/stripe", tags=["stripe"])
api_router.include_router(billing.router, prefix="/billing", tags=["billing"])
api_router.include_router(enquiries.router, tags=["enquiries"])
api_router.include_router(tenant_profile.router, prefix="/profile", tags=["profile"])
api_router.include_router(tenant_profile.router, prefix="/profile/fees", tags=["profile"])
# api_router.include_router(tenants.router, prefix="/tenants", tags=["tenants"])
# api_router.include_router(members.router, prefix="/members", tags=["members"])
