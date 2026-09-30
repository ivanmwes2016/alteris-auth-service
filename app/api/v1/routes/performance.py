"""
Two endpoints for the Academic Performance page. tenant_id always comes
from the authenticated user, never from the request body/query.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.auth import get_current_tenant_id, get_current_user
from app.core.db import get_db
from app.crud.performance import get_performance, get_performance_page, sync_marks
from app.db.models.users import User
from app.db.schemas.performance import PerformancePage, PerformanceRead, PerformanceUpdate
from app.helpers.pagination import PageParams, page_params

router = APIRouter()


@router.get("", response_model=PerformanceRead | PerformancePage)
async def get_class_performance(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    class_name: str = Query(..., alias="class", min_length=1),
    term: str = Query(..., min_length=1),
    year: int = Query(...),
    params: PageParams = Depends(page_params),
) -> PerformanceRead | PerformancePage:
    """
    Without `?page=`: every mark for the class/term/year, as before. With it: one page
    of the class roster ranked by average (search: student name), plus class-wide
    subjects, averages, trends and summary.
    """
    tenant_id = await get_current_tenant_id(db, current_user)
    if params.page is not None:
        return await get_performance_page(
            db,
            tenant_id=tenant_id,
            class_name=class_name,
            term_label=term,
            year=year,
            params=params,
        )
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
    Replaces the marks of the students in the payload for the given class/term/year;
    students not in it keep theirs. For a student in the payload, a blank/omitted
    score for a subject removes any previously-saved score for it.
    """
    tenant_id = await get_current_tenant_id(db, current_user)
    return await sync_marks(db, tenant_id=tenant_id, data=data)
