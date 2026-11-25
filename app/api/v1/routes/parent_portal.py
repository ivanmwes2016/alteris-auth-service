"""
Parents in the app (app.crud.parent_access).

/parent/...: a signed-in parent's own children. Every child route checks the child
is linked to the caller (404 otherwise), and needs no school membership.
/parent-invites/...: the invite page, used before anyone is signed in. The token is
sent in the body, so it stays out of server logs.
"""

import uuid
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import Client

from app.api.v1.routes.auth import get_current_user
from app.core.db import get_db
from app.core.supabase import get_supabase
from app.crud import attendance_reports, parent_access, performance_release, sick_bay
from app.crud.student_behaviour_profile import get_or_create_profile as behaviour_profile
from app.crud.student_medical_profile import get_or_create_profile as medical_profile
from app.db.models.behaviour import StudentBehaviourProfile
from app.db.models.medical_note import StudentMedicalProfile
from app.db.models.users import User
from app.db.schemas.attendance import StudentReportRead
from app.db.schemas.behaviour_profile import BehaviourProfileRead
from app.db.schemas.medical_profile import MedicalProfileRead
from app.db.schemas.parent_access import (
    ChildRead,
    ChildTermResults,
    ChildTimetableRead,
    InviteToken,
    ParentInviteAccept,
    ParentInviteAccepted,
    ParentInvitePreview,
    PushSettingsRead,
    PushSettingsUpdate,
)
from app.db.schemas.sick_bay import VisitRead
from app.helpers import rate_limit

router = APIRouter()
invite_router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]

ATTENDANCE_DEFAULT_DAYS = 90


# ---------------------------------------------------------------------------
# The parent's children
# ---------------------------------------------------------------------------
@router.get("/children", response_model=list[ChildRead])
async def list_children(db: Db, user: CurrentUser) -> list[ChildRead]:
    """Every child linked to the caller, across schools. Empty for anyone else."""
    return await parent_access.children(db, user)


@router.get("/children/{student_id}/attendance", response_model=StudentReportRead)
async def child_attendance(
    student_id: uuid.UUID,
    db: Db,
    user: CurrentUser,
    start: date | None = None,
    end: date | None = None,
) -> StudentReportRead:
    """Rates and marks between `start` and `end` (the last 90 days by default)."""
    student = await parent_access.require_child(db, user, student_id)
    end = end or await parent_access.school_today(db, student.tenant_id)
    start = start or end - timedelta(days=ATTENDANCE_DEFAULT_DAYS)
    return await attendance_reports.school_student_report(
        db, student.tenant_id, student.id, start=start, end=end
    )


@router.get("/children/{student_id}/results", response_model=list[ChildTermResults])
async def child_results(student_id: uuid.UUID, db: Db, user: CurrentUser) -> list[ChildTermResults]:
    """The child's term marks the school has published, newest first."""
    student = await parent_access.require_child(db, user, student_id)
    return await performance_release.child_results(db, student)


@router.get("/children/{student_id}/timetable", response_model=ChildTimetableRead)
async def child_timetable(student_id: uuid.UUID, db: Db, user: CurrentUser) -> ChildTimetableRead:
    student = await parent_access.require_child(db, user, student_id)
    return await parent_access.child_timetable(db, student)


@router.get("/children/{student_id}/medical", response_model=MedicalProfileRead)
async def child_medical(student_id: uuid.UUID, db: Db, user: CurrentUser) -> StudentMedicalProfile:
    student = await parent_access.require_child(db, user, student_id)
    return await medical_profile(db, student_id=student.id, tenant_id=student.tenant_id)


@router.get("/children/{student_id}/sick-bay", response_model=list[VisitRead])
async def child_sick_bay(
    student_id: uuid.UUID,
    db: Db,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[VisitRead]:
    """The child's sick bay visits, newest first."""
    student = await parent_access.require_child(db, user, student_id)
    return await sick_bay.student_visits(db, student, limit=limit)


@router.get("/children/{student_id}/behaviour", response_model=BehaviourProfileRead)
async def child_behaviour(
    student_id: uuid.UUID, db: Db, user: CurrentUser
) -> StudentBehaviourProfile:
    student = await parent_access.require_child(db, user, student_id)
    return await behaviour_profile(db, student_id=student.id, tenant_id=student.tenant_id)


@router.get("/push-settings", response_model=PushSettingsRead)
async def get_push_settings(db: Db, user: CurrentUser) -> PushSettingsRead:
    return await parent_access.push_settings(db, user)


@router.patch("/push-settings", response_model=PushSettingsRead)
async def update_push_settings(
    payload: PushSettingsUpdate, db: Db, user: CurrentUser
) -> PushSettingsRead:
    """Turn kinds of push on or off; kinds left out stay as they are."""
    return await parent_access.update_push_settings(db, user, payload)


# ---------------------------------------------------------------------------
# The invite page (no one signed in)
# ---------------------------------------------------------------------------
@invite_router.post("/preview", response_model=ParentInvitePreview)
async def preview_invite(payload: InviteToken, request: Request, db: Db) -> ParentInvitePreview:
    await rate_limit.enforce("parent_invite_ip", rate_limit.client_ip(request))
    return await parent_access.preview_invite(db, payload.token)


@invite_router.post("/accept", response_model=ParentInviteAccepted)
async def accept_invite(
    payload: ParentInviteAccept,
    request: Request,
    db: Db,
    supabase: Client = Depends(get_supabase),
) -> ParentInviteAccepted:
    """Set a password (unless the email already has a login) and link it to the parent."""
    await rate_limit.enforce("parent_invite_ip", rate_limit.client_ip(request))
    return await parent_access.accept_invite(db, supabase, payload.token, payload.password)
