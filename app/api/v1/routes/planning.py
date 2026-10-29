"""
Endpoints for lesson planning: schemes of work, lesson plans, the record of
work, comments, attachments and reports. Everyone who takes registers can use
them; approvers also approve schemes. tenant_id always comes from the
authenticated user, and every id is checked against it (see app/crud/planning.py).
"""

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import Client

from app.api.v1.routes.auth import get_current_user
from app.core.db import get_db
from app.core.supabase import get_supabase
from app.crud import attendance, planning, planning_reports
from app.db.models.users import User
from app.db.schemas.planning import (
    AttachmentIn,
    AttachmentRead,
    CommentIn,
    CommentRead,
    EntriesWrite,
    EntryIn,
    PlanningGapsRead,
    PlanRead,
    PlanSaveResult,
    PlanSummary,
    PlanVersionRead,
    PlanWrite,
    RecordOfWorkRead,
    RecordRead,
    RecordWrite,
    SchemeCreate,
    SchemeProgressRead,
    SchemeRead,
    SchemeStatus,
    SchemeSummary,
    SendBack,
    UploadUrlRead,
    UploadUrlRequest,
    WeekRead,
)

router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_db)]
Storage = Annotated[Client, Depends(get_supabase)]


async def _viewer(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> attendance.Viewer:
    # The same people as registers: Teachers, Head Teachers, Admins and the Owner.
    return await attendance.get_viewer(db, current_user)


Viewer = Annotated[attendance.Viewer, Depends(_viewer)]


# ---------------------------------------------------------------------------
# The week
# ---------------------------------------------------------------------------
@router.get("/week", response_model=WeekRead)
async def get_week(
    db: Db,
    viewer: Viewer,
    day: Annotated[date | None, Query(alias="date")] = None,
    scope: Literal["mine", "all"] = "mine",
) -> WeekRead:
    """The week (Monday to Sunday) holding `date`: each lesson with its plan and record."""
    return await planning_reports.get_week(db, viewer, day, mine_only=scope == "mine")


# ---------------------------------------------------------------------------
# Schemes of work
# ---------------------------------------------------------------------------
@router.get("/schemes", response_model=list[SchemeSummary])
async def list_schemes(
    db: Db,
    viewer: Viewer,
    term: str | None = None,
    year: int | None = None,
    class_name: Annotated[str | None, Query(alias="className")] = None,
    subject_id: Annotated[uuid.UUID | None, Query(alias="subjectId")] = None,
    scheme_status: Annotated[SchemeStatus | None, Query(alias="status")] = None,
) -> list[SchemeSummary]:
    return await planning.list_schemes(
        db,
        viewer,
        term=term,
        year=year,
        class_name=class_name,
        subject_id=subject_id,
        scheme_status=scheme_status,
    )


@router.post("/schemes", response_model=SchemeRead, status_code=status.HTTP_201_CREATED)
async def create_scheme(
    payload: SchemeCreate, db: Db, viewer: Viewer, supabase: Storage
) -> SchemeRead:
    """A new draft, empty or copied from an earlier scheme for the same class and subject."""
    return await planning.create_scheme(db, supabase, viewer, payload)


@router.get("/schemes/{scheme_id}", response_model=SchemeRead)
async def get_scheme(scheme_id: uuid.UUID, db: Db, viewer: Viewer, supabase: Storage) -> SchemeRead:
    return await planning.get_scheme(db, supabase, viewer, scheme_id)


@router.delete("/schemes/{scheme_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scheme(scheme_id: uuid.UUID, db: Db, viewer: Viewer) -> Response:
    await planning.delete_scheme(db, viewer, scheme_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/schemes/{scheme_id}/entries", response_model=SchemeRead)
async def write_entries(
    scheme_id: uuid.UUID, payload: EntriesWrite, db: Db, viewer: Viewer, supabase: Storage
) -> SchemeRead:
    """Every entry: ids change, no id adds, left out removes. Resubmits an approved scheme."""
    return await planning.write_entries(db, supabase, viewer, scheme_id, payload)


@router.put("/schemes/{scheme_id}/entries/{entry_id}", response_model=SchemeRead)
async def write_entry(
    scheme_id: uuid.UUID,
    entry_id: uuid.UUID,
    payload: EntryIn,
    db: Db,
    viewer: Viewer,
    supabase: Storage,
) -> SchemeRead:
    """One entry. Resubmits an approved scheme."""
    return await planning.write_entry(db, supabase, viewer, scheme_id, entry_id, payload)


@router.post("/schemes/{scheme_id}/submit", response_model=SchemeRead)
async def submit_scheme(
    scheme_id: uuid.UUID, db: Db, viewer: Viewer, supabase: Storage, background: BackgroundTasks
) -> SchemeRead:
    return await planning.submit_scheme(db, supabase, viewer, scheme_id, background)


@router.post("/schemes/{scheme_id}/approve", response_model=SchemeRead)
async def approve_scheme(
    scheme_id: uuid.UUID, db: Db, viewer: Viewer, supabase: Storage, background: BackgroundTasks
) -> SchemeRead:
    """Approvers."""
    return await planning.approve_scheme(db, supabase, viewer, scheme_id, background)


@router.post("/schemes/{scheme_id}/send-back", response_model=SchemeRead)
async def send_back_scheme(
    scheme_id: uuid.UUID,
    payload: SendBack,
    db: Db,
    viewer: Viewer,
    supabase: Storage,
    background: BackgroundTasks,
) -> SchemeRead:
    """Approvers. The comment starts the scheme's thread."""
    return await planning.send_back_scheme(
        db, supabase, viewer, scheme_id, payload.comment, background
    )


@router.get("/schemes/{scheme_id}/comments", response_model=list[CommentRead])
async def list_scheme_comments(scheme_id: uuid.UUID, db: Db, viewer: Viewer) -> list[CommentRead]:
    return await planning.list_comments(db, viewer, scheme_id=scheme_id)


@router.post(
    "/schemes/{scheme_id}/comments", response_model=CommentRead, status_code=status.HTTP_201_CREATED
)
async def add_scheme_comment(
    scheme_id: uuid.UUID, payload: CommentIn, db: Db, viewer: Viewer, background: BackgroundTasks
) -> CommentRead:
    return await planning.add_comment(db, viewer, payload, background, scheme_id=scheme_id)


@router.post(
    "/schemes/{scheme_id}/entries/{entry_id}/attachments",
    response_model=AttachmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def attach_to_entry(
    scheme_id: uuid.UUID,
    entry_id: uuid.UUID,
    payload: AttachmentIn,
    db: Db,
    viewer: Viewer,
    supabase: Storage,
) -> AttachmentRead:
    return await planning.attach_to_entry(db, supabase, viewer, scheme_id, entry_id, payload)


@router.get("/schemes/{scheme_id}/record-of-work", response_model=RecordOfWorkRead)
async def get_record_of_work(scheme_id: uuid.UUID, db: Db, viewer: Viewer) -> RecordOfWorkRead:
    """Every lesson of the scheme's class and subject that term, and what it covered."""
    return await planning_reports.record_of_work(db, viewer, scheme_id)


# ---------------------------------------------------------------------------
# Lesson plans and the record of work
# ---------------------------------------------------------------------------
@router.get("/plans", response_model=list[PlanSummary])
async def list_plans(
    db: Db,
    viewer: Viewer,
    term: str | None = None,
    year: int | None = None,
    class_name: Annotated[str | None, Query(alias="className")] = None,
    subject_id: Annotated[uuid.UUID | None, Query(alias="subjectId")] = None,
    week: int | None = None,
    author_id: Annotated[uuid.UUID | None, Query(alias="authorId")] = None,
) -> list[PlanSummary]:
    return await planning.list_plans(
        db,
        viewer,
        term=term,
        year=year,
        class_name=class_name,
        subject_id=subject_id,
        week=week,
        author_id=author_id,
    )


@router.get("/plans/{plan_id}", response_model=PlanRead)
async def get_plan(plan_id: uuid.UUID, db: Db, viewer: Viewer, supabase: Storage) -> PlanRead:
    return await planning.get_plan(db, supabase, viewer, plan_id)


@router.put("/plans/{plan_id}", response_model=PlanSaveResult)
async def save_plan(
    plan_id: uuid.UUID, payload: PlanWrite, db: Db, viewer: Viewer, supabase: Storage
) -> PlanSaveResult:
    """
    Creates or replaces the plan with this id (chosen by the phone). An older
    save than the one stored changes nothing and comes back with `stale`.
    """
    return await planning.save_plan(db, supabase, viewer, plan_id, payload)


@router.delete("/plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(plan_id: uuid.UUID, db: Db, viewer: Viewer) -> Response:
    await planning.delete_plan(db, viewer, plan_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/plans/{plan_id}/versions", response_model=list[PlanVersionRead])
async def get_plan_versions(plan_id: uuid.UUID, db: Db, viewer: Viewer) -> list[PlanVersionRead]:
    """Earlier versions, newest first."""
    return await planning.plan_versions(db, viewer, plan_id)


@router.get("/plans/{plan_id}/comments", response_model=list[CommentRead])
async def list_plan_comments(plan_id: uuid.UUID, db: Db, viewer: Viewer) -> list[CommentRead]:
    return await planning.list_comments(db, viewer, plan_id=plan_id)


@router.post(
    "/plans/{plan_id}/comments", response_model=CommentRead, status_code=status.HTTP_201_CREATED
)
async def add_plan_comment(
    plan_id: uuid.UUID, payload: CommentIn, db: Db, viewer: Viewer, background: BackgroundTasks
) -> CommentRead:
    return await planning.add_comment(db, viewer, payload, background, plan_id=plan_id)


@router.post(
    "/plans/{plan_id}/attachments",
    response_model=AttachmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def attach_to_plan(
    plan_id: uuid.UUID, payload: AttachmentIn, db: Db, viewer: Viewer, supabase: Storage
) -> AttachmentRead:
    return await planning.attach_to_plan(db, supabase, viewer, plan_id, payload)


@router.put("/records", response_model=RecordRead)
async def save_record(payload: RecordWrite, db: Db, viewer: Viewer) -> RecordRead:
    """
    A lesson's record of work, after it happened. The latest record wins.
    Not taught needs no plan; Taught and Partly taught do.
    """
    return await planning.save_record(db, viewer, payload)


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------
@router.post("/attachments/upload-url", response_model=UploadUrlRead)
async def create_upload_url(
    payload: UploadUrlRequest, viewer: Viewer, supabase: Storage
) -> UploadUrlRead:
    """Where to upload a photo or PDF; then attach the returned path to an entry or a plan."""
    return await planning.upload_url(supabase, viewer, payload)


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    attachment_id: uuid.UUID, db: Db, viewer: Viewer, supabase: Storage
) -> Response:
    await planning.remove_attachment(db, supabase, viewer, attachment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
@router.get("/reports/gaps", response_model=PlanningGapsRead)
async def get_gaps(db: Db, viewer: Viewer, start: date, end: date) -> PlanningGapsRead:
    """
    Unplanned, unrecorded and late lessons: teachers see their own, approvers
    everyone's and also records that disagree with the register.
    """
    return await planning_reports.gaps(db, viewer, start=start, end=end)


@router.get("/reports/progress", response_model=list[SchemeProgressRead])
async def get_progress(db: Db, viewer: Viewer, term: str, year: int) -> list[SchemeProgressRead]:
    """Each stream's place in each of the term's schemes."""
    return await planning_reports.progress(db, viewer, term=term, year=year)
