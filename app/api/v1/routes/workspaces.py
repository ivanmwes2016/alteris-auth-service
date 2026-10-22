from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import Client

from app.core.db import get_db
from app.core.supabase import get_supabase
from app.db.models.tenant import Tenant

router = APIRouter()

BUCKET_NAME = "client-logos"


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
