"""
Two endpoints for the Academic Terms settings manager. tenant_id always
comes from the authenticated user, never from the request body/query.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.auth import get_current_tenant_id, get_current_user
from app.core.db import get_db
from app.crud.academic_term import get_or_create_settings, update_settings
from app.db.models.academic_term import AcademicTermSettings
from app.db.models.users import User
from app.db.schemas.academic_term import TermSettingsRead, TermSettingsUpdate

router = APIRouter()


@router.get("", response_model=TermSettingsRead)
async def get_term_settings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AcademicTermSettings:
    tenant_id = await get_current_tenant_id(db, current_user)
    return await get_or_create_settings(db, tenant_id=tenant_id)


@router.patch("", response_model=TermSettingsRead)
async def update_term_settings(
    data: TermSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AcademicTermSettings:
    tenant_id = await get_current_tenant_id(db, current_user)
    return await update_settings(db, tenant_id=tenant_id, data=data)
