"""
A student's NFC card (app.crud.student_cards). Linking cards is part of a student's
details, so it follows the same permission. tenant_id always comes from the
authenticated user.
"""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.auth import get_current_user
from app.core.db import get_db
from app.crud import student_access, student_cards
from app.db.models.users import User
from app.db.schemas.student_card import CardLink, CardRead

router = APIRouter()


@router.get("", response_model=CardRead | None)
async def get_student_card(
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CardRead | None:
    caller = await student_access.require(db, current_user, "edit_details")
    card = await student_cards.get_card(db, caller.tenant_id, student_id)
    return CardRead.model_validate(card) if card else None


@router.put("", response_model=CardRead)
async def link_student_card(
    student_id: uuid.UUID,
    data: CardLink,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CardRead:
    caller = await student_access.require(db, current_user, "edit_details")
    card = await student_cards.link_card(
        db,
        tenant_id=caller.tenant_id,
        student_id=student_id,
        uid=data.uid,
        user=current_user,
    )
    return CardRead.model_validate(card)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_student_card(
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    caller = await student_access.require(db, current_user, "edit_details")
    await student_cards.unlink_card(db, caller.tenant_id, student_id)
