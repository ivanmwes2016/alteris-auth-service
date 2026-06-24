from uuid import UUID
from sqlalchemy import select
from slugify import slugify

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import Client

from app.api.v1.routes.auth import get_current_user
from app.core.db import get_db
from app.core.supabase import get_supabase
from app.db.models.role import Role
from app.db.models.tenant import Tenant
from pydantic import BaseModel

from app.db.models.tenant_member import TenantMember
from app.db.models.users import User

router = APIRouter()

BUCKET_NAME = "client-logos"


class UpdateWorkspaceRequest(BaseModel):
    name: str
    workspaceId:str


@router.patch("/onboarding")
async def update_workspace( payload: UpdateWorkspaceRequest, db: AsyncSession = Depends(get_db), current_user :User = Depends(get_current_user)):
    slug = slugify(payload.name)
    tenant = Tenant(
        name=payload.name,
        slug=slug,
        workspace_id=payload.workspaceId
    )

    existing_tenant = await db.execute(
        select(Tenant).where(Tenant.slug == slug)
    )

    if existing_tenant.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Workspace already exists")
    


    db.add(tenant)
    await db.flush()

    admin_role = await get_role_by_name(db, "admin")

    member = TenantMember( tenant_id=tenant.id, user_id=current_user.id, role_id=admin_role.id, status="active")
    if not member:
        raise HTTPException(status_code=409, detail="No team member for that creteria")

    db.add(member)
    await db.commit()
    await db.refresh(tenant)

    return {
        "tenant_id": tenant.id,
        "name": tenant.name,
        "role": "admin",
    }





@router.post("/{workspace_id}/logo")
async def upload_workspace_logo(
    workspace_id: str,
    logo: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    supabase: Client = Depends(get_supabase),
):
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

    extension = logo.filename.split(".")[-1]
    logo_path = f"{workspace_id}/logo.{extension}"

    supabase.storage.from_(BUCKET_NAME).upload(
        path=logo_path,
        file=file_bytes,
        file_options={
            "content-type": logo.content_type,
            "upsert": "true",
        },
    )

    result = await db.execute( select(Tenant).where(Tenant.workspace_id == workspace_id))
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



async def get_role_by_name(db: AsyncSession, name: str):
    result = await db.execute(select(Role).where(Role.name == name))
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(status_code=500, detail=f"Role '{name}' does not exist")

    return role

async def get_tenant_member(db: AsyncSession, tenant_id: UUID, user_id: UUID):
    result = await db.execute(
        select(TenantMember).where(
            TenantMember.tenant_id == tenant_id,
            TenantMember.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()