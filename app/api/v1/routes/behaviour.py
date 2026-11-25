"""
Two endpoints for the whole Behaviour tab. tenant_id always comes from the
authenticated user, never from the request body/query.
"""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.auth import get_current_user
from app.core.db import get_db
from app.crud import student_access
from app.crud.student_behaviour_profile import get_or_create_profile, sync_profile
from app.db.models.behaviour import StudentBehaviourProfile
from app.db.models.student import Student
from app.db.models.users import User
from app.db.schemas.behaviour_profile import BehaviourProfileBulkUpdate, BehaviourProfileRead
from app.services import parent_push

router = APIRouter()


@router.get("", response_model=BehaviourProfileRead)
async def get_behaviour_profile(
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StudentBehaviourProfile:
    caller = await student_access.require(db, current_user, "view_records")
    await student_access.ensure_in_school(db, caller.tenant_id, student_id)
    return await get_or_create_profile(db, student_id=student_id, tenant_id=caller.tenant_id)


@router.patch("", response_model=BehaviourProfileRead)
async def sync_behaviour_profile(
    student_id: uuid.UUID,
    data: BehaviourProfileBulkUpdate,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StudentBehaviourProfile:
    """
    Send the profile's scalar fields plus whichever lists changed.
    Each list item's `id`: present+matching -> update, missing -> create.
    Any existing row whose id is left out of the list gets deleted.
    Omit a list key entirely to leave that section untouched.
    """
    caller = await student_access.require(db, current_user, "edit_behaviour")
    await student_access.ensure_in_school(db, caller.tenant_id, student_id)
    before = await get_or_create_profile(db, student_id=student_id, tenant_id=caller.tenant_id)
    known = {i.id for i in before.incidents} | {r.id for r in before.recognitions}
    profile = await sync_profile(db, student_id=student_id, tenant_id=caller.tenant_id, data=data)
    student = await db.get(Student, student_id)
    if student is not None:
        background.add_task(
            parent_push.notify_parents,
            parent_push.new_behaviour_notices(student, profile, known=known),
        )
    return profile
