"""
Endpoints for attendance: registers, corrections, reports and the settings
they depend on. tenant_id always comes from the authenticated user, and every
id is checked against it (see app/crud/attendance.py for the rules).
"""

import uuid
from datetime import date, time
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.auth import get_current_user
from app.core.db import get_db
from app.crud import attendance, attendance_reports, attendance_settings, team
from app.db.models.users import User
from app.db.schemas.attendance import (
    AttendanceSettingsRead,
    AttendanceSettingsUpdate,
    CorrectionCreate,
    CorrectionDecision,
    CorrectionRequestRead,
    CorrectionResult,
    GapsRead,
    MarkChangeRead,
    NoSchoolDayCreate,
    NoSchoolDayRead,
    OverviewRead,
    PushTokenRegistration,
    RegisterKey,
    RegisterRead,
    RegisterSave,
    RegisterSaveResult,
    StaffAccountRead,
    StaffAccountWrite,
    StudentReportRead,
    StudentSubjectsRead,
    StudentSubjectsWrite,
    TermDates,
    TermDatesWrite,
    TodayRead,
)

router = APIRouter()
push_router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_db)]


async def _viewer(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> attendance.Viewer:
    return await attendance.get_viewer(db, current_user)


Viewer = Annotated[attendance.Viewer, Depends(_viewer)]


def _register_key(
    kind: Literal["daily", "lesson"],
    day: Annotated[date, Query(alias="date")],
    class_name: Annotated[str | None, Query(alias="className", max_length=100)] = None,
    stream: Annotated[str | None, Query(max_length=100)] = None,
    lesson_id: Annotated[uuid.UUID | None, Query(alias="lessonId")] = None,
    subject_id: Annotated[uuid.UUID | None, Query(alias="subjectId")] = None,
    starts_at: Annotated[time | None, Query(alias="startsAt")] = None,
) -> RegisterKey:
    try:
        return RegisterKey(
            kind=kind,
            date=day,
            class_name=class_name,
            stream=stream,
            lesson_id=lesson_id,
            subject_id=subject_id,
            starts_at=starts_at,
        )
    except ValidationError as exc:
        raise attendance.invalid(exc.errors()[0]["msg"]) from exc


# ---------------------------------------------------------------------------
# Registers
# ---------------------------------------------------------------------------
@router.get("/today", response_model=TodayRead)
async def get_today(
    db: Db, viewer: Viewer, day: Annotated[date | None, Query(alias="date")] = None
) -> TodayRead:
    """The day's registers: mine, the daily ones, every timetabled lesson and ad-hoc ones."""
    return await attendance.get_today(db, viewer, day)


@router.get("/register", response_model=RegisterRead)
async def get_register(
    db: Db,
    viewer: Viewer,
    key: Annotated[RegisterKey, Depends(_register_key)],
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> RegisterRead:
    """One register's students and marks. `q` searches names and admission numbers."""
    return await attendance.get_register(db, viewer, key, q)


@router.put("/register/marks", response_model=RegisterSaveResult)
async def save_marks(db: Db, viewer: Viewer, payload: RegisterSave) -> RegisterSaveResult:
    """
    Saves marks taken today, or offline on the register's day within 72 hours.
    The latest tap wins: older marks come back in `skipped`. 409 once a register
    has closed: send a correction instead.
    """
    return await attendance.save_marks(db, viewer, payload)


@router.get(
    "/registers/{register_id}/students/{student_id}/history",
    response_model=list[MarkChangeRead],
)
async def get_mark_history(
    register_id: uuid.UUID, student_id: uuid.UUID, db: Db, viewer: Viewer
) -> list[MarkChangeRead]:
    return await attendance.mark_history(db, viewer, register_id, student_id)


# ---------------------------------------------------------------------------
# Corrections
# ---------------------------------------------------------------------------
@router.post("/corrections", response_model=CorrectionResult, status_code=status.HTTP_201_CREATED)
async def create_correction(
    payload: CorrectionCreate, background: BackgroundTasks, db: Db, viewer: Viewer
) -> CorrectionResult:
    """Approvers' corrections apply at once; anyone else's becomes a request."""
    return await attendance.correct(db, viewer, payload, background)


@router.get("/corrections", response_model=list[CorrectionRequestRead])
async def list_corrections(
    db: Db,
    viewer: Viewer,
    scope: Literal["mine", "review"] = "mine",
    status_: Annotated[
        Literal["pending", "accepted", "rejected"] | None, Query(alias="status")
    ] = None,
) -> list[CorrectionRequestRead]:
    return await attendance.list_requests(db, viewer, scope=scope, status_=status_)


@router.get("/corrections/{request_id}", response_model=CorrectionRequestRead)
async def get_correction(request_id: uuid.UUID, db: Db, viewer: Viewer) -> CorrectionRequestRead:
    return await attendance.get_request(db, viewer, request_id)


@router.post("/corrections/{request_id}/accept", response_model=CorrectionRequestRead)
async def accept_correction(
    request_id: uuid.UUID,
    payload: CorrectionDecision,
    background: BackgroundTasks,
    db: Db,
    viewer: Viewer,
) -> CorrectionRequestRead:
    return await attendance.decide(
        db, viewer, request_id, accept=True, note=payload.note, background=background
    )


@router.post("/corrections/{request_id}/reject", response_model=CorrectionRequestRead)
async def reject_correction(
    request_id: uuid.UUID,
    payload: CorrectionDecision,
    background: BackgroundTasks,
    db: Db,
    viewer: Viewer,
) -> CorrectionRequestRead:
    return await attendance.decide(
        db, viewer, request_id, accept=False, note=payload.note, background=background
    )


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
@router.get("/reports/overview", response_model=OverviewRead)
async def get_overview(
    db: Db,
    viewer: Viewer,
    start: date,
    end: date,
    class_name: Annotated[str | None, Query(alias="className", max_length=100)] = None,
    stream: Annotated[str | None, Query(max_length=100)] = None,
) -> OverviewRead:
    return await attendance_reports.overview(
        db, viewer, start=start, end=end, class_name=class_name, stream=stream
    )


@router.get("/reports/students/{student_id}", response_model=StudentReportRead)
async def get_student_report(
    student_id: uuid.UUID, db: Db, viewer: Viewer, start: date, end: date
) -> StudentReportRead:
    return await attendance_reports.student_report(db, viewer, student_id, start=start, end=end)


@router.get("/reports/gaps", response_model=GapsRead)
async def get_gaps(db: Db, viewer: Viewer, start: date, end: date) -> GapsRead:
    """Approvers see every gap; teachers see their own."""
    return await attendance_reports.gaps(db, viewer, start=start, end=end)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
@router.get("/settings", response_model=AttendanceSettingsRead)
async def get_settings(viewer: Viewer) -> AttendanceSettingsRead:
    return attendance_settings.settings_read(viewer)


@router.patch("/settings", response_model=AttendanceSettingsRead)
async def update_settings(
    payload: AttendanceSettingsUpdate, db: Db, viewer: Viewer
) -> AttendanceSettingsRead:
    """Owner and Admins. A null timezone goes back to the country's."""
    return await attendance_settings.update_settings(db, viewer, payload)


@router.get("/term-dates", response_model=list[TermDates])
async def get_term_dates(db: Db, viewer: Viewer) -> list[TermDates]:
    return await attendance_settings.list_term_dates(db, viewer.tenant_id)


@router.put("/term-dates", response_model=list[TermDates])
async def replace_term_dates(payload: TermDatesWrite, db: Db, viewer: Viewer) -> list[TermDates]:
    """Approvers. Replaces every term's dates; terms can't overlap."""
    return await attendance_settings.replace_term_dates(db, viewer, payload)


@router.get("/no-school-days", response_model=list[NoSchoolDayRead])
async def get_no_school_days(
    db: Db, viewer: Viewer, year: int | None = None
) -> list[NoSchoolDayRead]:
    return await attendance_settings.list_no_school_days(db, viewer, year=year)


@router.post("/no-school-days", response_model=NoSchoolDayRead, status_code=status.HTTP_201_CREATED)
async def add_no_school_day(payload: NoSchoolDayCreate, db: Db, viewer: Viewer) -> NoSchoolDayRead:
    return await attendance_settings.add_no_school_day(db, viewer, payload)


@router.delete("/no-school-days/{day_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_no_school_day(day_id: uuid.UUID, db: Db, viewer: Viewer) -> Response:
    await attendance_settings.remove_no_school_day(db, viewer, day_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/students/{student_id}/subjects", response_model=StudentSubjectsRead)
async def get_student_subjects(
    student_id: uuid.UUID, db: Db, viewer: Viewer
) -> StudentSubjectsRead:
    """An empty list means the student takes every subject their class takes."""
    return await attendance_settings.get_student_subjects(db, viewer, student_id)


@router.put("/students/{student_id}/subjects", response_model=StudentSubjectsRead)
async def set_student_subjects(
    student_id: uuid.UUID, payload: StudentSubjectsWrite, db: Db, viewer: Viewer
) -> StudentSubjectsRead:
    return await attendance_settings.set_student_subjects(db, viewer, student_id, payload)


@router.put("/staff/{staff_id}/account", response_model=StaffAccountRead)
async def set_staff_account(
    staff_id: uuid.UUID, payload: StaffAccountWrite, db: Db, viewer: Viewer
) -> StaffAccountRead:
    """Approvers. Links the app account a staff member signs in with (null unlinks)."""
    return await attendance_settings.set_staff_account(db, viewer, staff_id, payload)


# ---------------------------------------------------------------------------
# Push tokens: mounted at /push-tokens, the same shape as the marking service's
# ---------------------------------------------------------------------------
@push_router.put("", status_code=status.HTTP_204_NO_CONTENT)
async def register_push_token(
    payload: PushTokenRegistration, db: Db, current_user: User = Depends(get_current_user)
) -> Response:
    """Notify this phone about attendance corrections. Calling it again is harmless."""
    actor = await team.get_actor(db, current_user)
    await attendance_settings.register_push_token(db, actor, payload.token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@push_router.delete("/{token}", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_push_token(
    token: str, db: Db, current_user: User = Depends(get_current_user)
) -> Response:
    """Stop notifying this phone (signing out). Unknown tokens are ignored."""
    actor = await team.get_actor(db, current_user)
    await attendance_settings.unregister_push_token(db, actor, token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
