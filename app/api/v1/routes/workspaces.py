from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import Client

from app.api.v1.routes.auth import get_current_user
from app.core.db import get_db
from app.core.supabase import get_supabase
from app.db.models.role import Role
from app.db.models.tenant import Tenant
from app.db.models.tenant_member import TenantMember
from app.db.models.users import User
from app.services.notifications import (
    notify_owner_school_received,
    notify_school_registered,
)
from app.services.school_approval import new_review_token, review_url

router = APIRouter()

BUCKET_NAME = "client-logos"


class UpdateWorkspaceRequest(BaseModel):
    name: str
    workspace_id: str


class WorkSpaceResponse(BaseModel):
    tenant_id: UUID
    name: str
    role: str


@router.patch("/onboarding")
async def update_workspace(
    payload: UpdateWorkspaceRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WorkSpaceResponse:
    slug = slugify(payload.name)
    review_token, review_token_hash = new_review_token()
    tenant = Tenant(
        name=payload.name,
        slug=slug,
        workspace_id=payload.workspace_id,
        owner_id=current_user.id,
        status="pending",
        review_token_hash=review_token_hash,
    )

    existing_tenant = await db.execute(select(Tenant).where(Tenant.slug == slug))

    if existing_tenant.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Workspace already exists")

    db.add(tenant)
    await db.flush()

    admin_role = await get_role_by_name(db, "admin")

    member = TenantMember(
        tenant_id=tenant.id,
        user_id=current_user.id,
        role_id=admin_role.id,
        status="active",
    )
    if not member:
        raise HTTPException(status_code=409, detail="No team member for that creteria")

    db.add(member)
    await db.commit()
    await db.refresh(tenant)

    background_tasks.add_task(
        notify_school_registered,
        school_name=tenant.name,
        country=tenant.country,
        plan=tenant.plan,
        workspace_id=tenant.workspace_id,
        owner_name=current_user.name,
        owner_email=current_user.email,
        review_url=review_url(review_token),
    )
    background_tasks.add_task(
        notify_owner_school_received,
        school_name=tenant.name,
        owner_name=current_user.name,
        owner_email=current_user.email,
    )

    return WorkSpaceResponse(
        tenant_id=tenant.id,
        name=tenant.name,
        role="admin",
    )


@router.post("/{workspace_id}/logo")
async def upload_workspace_logo(
    workspace_id: str,
    logo: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    supabase: Client = Depends(get_supabase),
) -> dict[str, str]:
    allowed_types = ["image/png", "image/jpeg", "image/webp"]

    if logo.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail="Logo must be PNG, JPG, or WebP",
        )

    file_bytes = await logo.read()

    if len(file_bytes) > 10 * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail="Logo must be less than 10MB",
        )

    logo_path = ""
    expires_in = 60 * 60 * 24 * 30  # 30 days

    if logo is not None and logo.filename is not None:
        extension = logo.filename.split(".")[-1]
        logo_path = f"{workspace_id}/logo.{extension}"

    await supabase.storage.from_(BUCKET_NAME).upload(
        path=logo_path,
        file=file_bytes,
        expires_in=expires_in,
        file_options={
            "content-type": logo.content_type,
            "upsert": "true",
        },
    )

    result = await db.execute(select(Tenant).where(Tenant.workspace_id == workspace_id))
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(status_code=404, detail="Workspace not found")

    tenant.logo_path = logo_path

    await db.commit()
    await db.refresh(tenant)

    return {
        "message": "Logo uploaded successfully",
        "logo_path": logo_path,
    }


async def get_role_by_name(db: AsyncSession, name: str) -> Role:
    result = await db.execute(select(Role).where(Role.name == name))
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(status_code=500, detail=f"Role '{name}' does not exist")

    return role


async def get_tenant_member(
    db: AsyncSession, tenant_id: UUID, user_id: UUID
) -> TenantMember | None:
    result = await db.execute(
        select(TenantMember).where(
            TenantMember.tenant_id == tenant_id,
            TenantMember.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()
