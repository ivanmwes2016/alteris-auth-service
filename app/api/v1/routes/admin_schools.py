"""
Reviewing new schools from the emailed link (see app/services/school_approval.py).
No sign-in: the one-time token in the link is the authority, and it is only ever
sent to ADMIN_NOTIFY_EMAILS. The token travels in the body, not the URL path, so it
stays out of access logs.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.helpers import rate_limit
from app.services import school_approval
from app.services.school_approval import ReviewDecision, SchoolReview

router = APIRouter()


class ReviewToken(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=256)


async def _limit(request: Request) -> None:
    await rate_limit.enforce("school_review_ip", rate_limit.client_ip(request))


@router.post("/review/preview", response_model=SchoolReview)
async def preview_school(
    payload: ReviewToken, request: Request, db: AsyncSession = Depends(get_db)
) -> SchoolReview:
    await _limit(request)
    return await school_approval.preview(db, payload.token)


@router.post("/review/approve", response_model=ReviewDecision)
async def approve_school(
    payload: ReviewToken,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> ReviewDecision:
    await _limit(request)
    decision, owner = await school_approval.approve(db, payload.token)
    background_tasks.add_task(
        school_approval.email_owner,
        decision,
        owner.email if owner else None,
        owner.name if owner else None,
    )
    return decision


@router.post("/review/reject", response_model=ReviewDecision)
async def reject_school(
    payload: ReviewToken,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> ReviewDecision:
    await _limit(request)
    decision, owner = await school_approval.reject(db, payload.token)
    background_tasks.add_task(
        school_approval.email_owner,
        decision,
        owner.email if owner else None,
        owner.name if owner else None,
    )
    return decision
