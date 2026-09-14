from fastapi import APIRouter

from app.api.v1.routes import stripe_webhooks

from .routes import (
    attendance,
    auth,
    behaviour,
    billing,
    classes,
    enquiries,
    health,
    medical,
    parents,
    students,
    subjects,
    tenant_profile,
    workspaces,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(workspaces.router, prefix="/workspaces", tags=["workspaces"])
api_router.include_router(stripe_webhooks.router, prefix="/stripe", tags=["stripe"])
api_router.include_router(billing.router, prefix="/billing", tags=["billing"])
api_router.include_router(enquiries.router, tags=["enquiries"])
api_router.include_router(tenant_profile.router, prefix="/profile", tags=["profile"])
api_router.include_router(tenant_profile.router, prefix="/profile/fees", tags=["profile"])
api_router.include_router(students.router, prefix="/students", tags=["students"])
api_router.include_router(parents.router, prefix="/parents", tags=["parents"])
api_router.include_router(classes.router, prefix="/classes", tags=["classes"])
api_router.include_router(subjects.router, prefix="/subjects", tags=["subjects"])
api_router.include_router(medical.router, prefix="/students/{student_id}/medical", tags=["medical"])
api_router.include_router(
    behaviour.router, prefix="/students/{student_id}/behaviour", tags=["behaviour"]
)
api_router.include_router(
    attendance.router, prefix="/students/{student_id}/attendance", tags=["attendance"]
)


# api_router.include_router(tenants.router, prefix="/tenants", tags=["tenants"])
# api_router.include_router(members.router, prefix="/members", tags=["members"])
