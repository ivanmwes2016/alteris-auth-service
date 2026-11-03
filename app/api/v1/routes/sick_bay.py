"""
The app's Health page: the sick bay and health alerts (app.crud.sick_bay).
tenant_id always comes from the authenticated user.
"""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.auth import get_current_user
from app.core.db import get_db
from app.crud import sick_bay
from app.db.models.users import User
from app.db.schemas.sick_bay import HealthAlerts, SickBayDay, VisitCreate, VisitRead, VisitUpdate

router = APIRouter()


@router.get("", response_model=SickBayDay)
async def get_sick_bay(
    day: date | None = Query(None, alias="date"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SickBayDay:
    """The school day's visits (today by default), plus any still open from earlier days."""
    caller = await sick_bay.get_caller(db, current_user)
    return await sick_bay.day_visits(db, caller, day)


@router.post("", response_model=VisitRead, status_code=status.HTTP_201_CREATED)
async def send_to_sick_bay(
    data: VisitCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VisitRead:
    caller = await sick_bay.get_caller(db, current_user)
    return await sick_bay.send(db, caller, data)


@router.get("/alerts", response_model=HealthAlerts)
async def get_health_alerts(
    class_name: str | None = Query(None, alias="className"),
    stream: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> HealthAlerts:
    """Students with allergies, conditions, medicines or emergency notes, optionally one class."""
    caller = await sick_bay.get_caller(db, current_user)
    return await sick_bay.alerts(db, caller, class_name=class_name, stream=stream)


@router.patch("/{visit_id}", response_model=VisitRead)
async def update_visit(
    visit_id: uuid.UUID,
    data: VisitUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VisitRead:
    """The nurse's record: arrived, temperature, treatment, notes, and where the student went."""
    caller = await sick_bay.get_caller(db, current_user)
    return await sick_bay.update(db, caller, visit_id, data)
