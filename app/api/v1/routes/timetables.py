"""
Endpoints for the Timetable page. tenant_id always comes from the
authenticated user. Reading is open to any active member; changes need the
Manage Timetable roles and a term that isn't past (see app/crud/timetable.py).
"""

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.auth import get_current_user
from app.core.db import get_db
from app.crud import timetable as timetables
from app.db.models.users import User
from app.db.schemas.timetable import (
    BellScheduleImpact,
    BellScheduleWrite,
    LessonCreate,
    LessonRead,
    LessonSwap,
    LessonUpdate,
    OptionsPeriodCreate,
    OptionsPeriodRead,
    TimetableCreate,
    TimetableRead,
    TimetableSummary,
)

router = APIRouter()


async def _viewer(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> timetables.Viewer:
    return await timetables.get_viewer(db, current_user)


@router.get("", response_model=list[TimetableSummary])
async def list_timetables(
    db: AsyncSession = Depends(get_db), viewer: timetables.Viewer = Depends(_viewer)
) -> list[TimetableSummary]:
    return await timetables.list_timetables(db, viewer)


@router.get("/term", response_model=TimetableRead)
async def get_term_timetable(
    term: str = Query(..., min_length=1),
    year: int = Query(...),
    db: AsyncSession = Depends(get_db),
    viewer: timetables.Viewer = Depends(_viewer),
) -> TimetableRead:
    """The timetable for one term, or 404 if the school hasn't made one yet."""
    return await timetables.get_timetable(db, viewer, term=term, year=year)


@router.post("", response_model=TimetableRead, status_code=status.HTTP_201_CREATED)
async def create_timetable(
    payload: TimetableCreate,
    db: AsyncSession = Depends(get_db),
    viewer: timetables.Viewer = Depends(_viewer),
) -> TimetableRead:
    return await timetables.create_timetable(db, viewer, payload)


@router.post("/{timetable_id}/bell-schedule/preview", response_model=BellScheduleImpact)
async def preview_bell_schedule(
    timetable_id: uuid.UUID,
    payload: BellScheduleWrite,
    db: AsyncSession = Depends(get_db),
    viewer: timetables.Viewer = Depends(_viewer),
) -> BellScheduleImpact:
    """How many lessons saving this bell schedule would remove or shorten. Changes nothing."""
    return await timetables.preview_bell_schedule(db, viewer, timetable_id, payload)


@router.put("/{timetable_id}/bell-schedule", response_model=TimetableRead)
async def save_bell_schedule(
    timetable_id: uuid.UUID,
    payload: BellScheduleWrite,
    db: AsyncSession = Depends(get_db),
    viewer: timetables.Viewer = Depends(_viewer),
) -> TimetableRead:
    return await timetables.save_bell_schedule(db, viewer, timetable_id, payload)


@router.post(
    "/{timetable_id}/lessons", response_model=LessonRead, status_code=status.HTTP_201_CREATED
)
async def create_lesson(
    timetable_id: uuid.UUID,
    payload: LessonCreate,
    db: AsyncSession = Depends(get_db),
    viewer: timetables.Viewer = Depends(_viewer),
) -> LessonRead:
    return await timetables.create_lesson(db, viewer, timetable_id, payload)


@router.post("/{timetable_id}/lessons/swap", response_model=list[LessonRead])
async def swap_lessons(
    timetable_id: uuid.UUID,
    payload: LessonSwap,
    db: AsyncSession = Depends(get_db),
    viewer: timetables.Viewer = Depends(_viewer),
) -> list[LessonRead]:
    return await timetables.swap_lessons(db, viewer, timetable_id, payload)


@router.patch("/{timetable_id}/lessons/{lesson_id}", response_model=LessonRead)
async def update_lesson(
    timetable_id: uuid.UUID,
    lesson_id: uuid.UUID,
    payload: LessonUpdate,
    db: AsyncSession = Depends(get_db),
    viewer: timetables.Viewer = Depends(_viewer),
) -> LessonRead:
    return await timetables.update_lesson(db, viewer, timetable_id, lesson_id, payload)


@router.delete("/{timetable_id}/lessons/{lesson_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lesson(
    timetable_id: uuid.UUID,
    lesson_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    viewer: timetables.Viewer = Depends(_viewer),
) -> Response:
    await timetables.delete_lesson(db, viewer, timetable_id, lesson_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{timetable_id}/options-periods",
    response_model=OptionsPeriodRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_options_period(
    timetable_id: uuid.UUID,
    payload: OptionsPeriodCreate,
    db: AsyncSession = Depends(get_db),
    viewer: timetables.Viewer = Depends(_viewer),
) -> OptionsPeriodRead:
    return await timetables.create_options_period(db, viewer, timetable_id, payload)


@router.delete(
    "/{timetable_id}/options-periods/{option_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_options_period(
    timetable_id: uuid.UUID,
    option_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    viewer: timetables.Viewer = Depends(_viewer),
) -> Response:
    await timetables.delete_options_period(db, viewer, timetable_id, option_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
