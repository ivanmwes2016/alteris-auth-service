"""
Two endpoints for the Academic Performance page. tenant_id always comes
from the authenticated user, never from the request body/query.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.auth import get_current_tenant_id, get_current_user
from app.core.db import get_db
from app.crud.performance import get_performance, sync_marks
from app.db.models.users import User
from app.db.schemas.performance import PerformanceRead, PerformanceUpdate

router = APIRouter()


@router.get("", response_model=PerformanceRead)
async def get_class_performance(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    class_name: str = Query(..., alias="class", min_length=1),
    term: str = Query(..., min_length=1),
    year: int = Query(...),
) -> PerformanceRead:
    tenant_id = await get_current_tenant_id(db, current_user)
    return await get_performance(
        db, tenant_id=tenant_id, class_name=class_name, term_label=term, year=year
    )


@router.patch("", response_model=PerformanceRead)
async def update_class_performance(
    data: PerformanceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PerformanceRead:
    """
    Always replaces the complete marks list for the given class/term/year.
    A blank/omitted score for a (student, subject) pair removes any
    previously-saved score for it.
    """
    tenant_id = await get_current_tenant_id(db, current_user)
    return await sync_marks(db, tenant_id=tenant_id, data=data)
