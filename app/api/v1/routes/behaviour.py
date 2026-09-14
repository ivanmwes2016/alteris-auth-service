"""
Two endpoints for the whole Behaviour tab. tenant_id always comes from the
authenticated user, never from the request body/query.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.auth import get_current_tenant_id, get_current_user
from app.core.db import get_db
from app.crud.student_behaviour_profile import get_or_create_profile, sync_profile
from app.db.models.behaviour import StudentBehaviourProfile
from app.db.models.users import User
from app.db.schemas.behaviour_profile import BehaviourProfileBulkUpdate, BehaviourProfileRead

router = APIRouter()


@router.get("", response_model=BehaviourProfileRead)
async def get_behaviour_profile(
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StudentBehaviourProfile:
    tenant_id = await get_current_tenant_id(db, current_user)
    return await get_or_create_profile(db, student_id=student_id, tenant_id=tenant_id)


@router.patch("", response_model=BehaviourProfileRead)
async def sync_behaviour_profile(
    student_id: uuid.UUID,
    data: BehaviourProfileBulkUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StudentBehaviourProfile:
    """
    Send the profile's scalar fields plus whichever lists changed.
    Each list item's `id`: present+matching -> update, missing -> create.
    Any existing row whose id is left out of the list gets deleted.
    Omit a list key entirely to leave that section untouched.
    """
    tenant_id = await get_current_tenant_id(db, current_user)
    return await sync_profile(db, student_id=student_id, tenant_id=tenant_id, data=data)
