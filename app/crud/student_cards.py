"""
Students' NFC cards. An admin links a card to a student; registers carry the serial
numbers so a teacher's phone can match a tap with no signal.
"""

import uuid
from collections.abc import Iterable

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.student import Student
from app.db.models.student_card import StudentCard
from app.db.models.users import User


def _taken(owner: str | None) -> HTTPException:
    who = owner or "another student"
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"This card is already linked to {who}. Remove it from them first.",
    )


async def _student(db: AsyncSession, tenant_id: uuid.UUID, student_id: uuid.UUID) -> Student:
    student = await db.scalar(
        select(Student).where(Student.id == student_id, Student.tenant_id == tenant_id)
    )
    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    return student


async def get_card(
    db: AsyncSession, tenant_id: uuid.UUID, student_id: uuid.UUID
) -> StudentCard | None:
    await _student(db, tenant_id, student_id)
    card: StudentCard | None = await db.scalar(
        select(StudentCard).where(
            StudentCard.tenant_id == tenant_id, StudentCard.student_id == student_id
        )
    )
    return card


async def link_card(
    db: AsyncSession, *, tenant_id: uuid.UUID, student_id: uuid.UUID, uid: str, user: User
) -> StudentCard:
    """Gives the student this card, replacing any they had. A card has one owner at a time."""
    card = await get_card(db, tenant_id, student_id)
    owner_id = await db.scalar(
        select(StudentCard.student_id).where(
            StudentCard.tenant_id == tenant_id, StudentCard.uid == uid
        )
    )
    if owner_id is not None and owner_id != student_id:
        raise _taken(await db.scalar(select(Student.name).where(Student.id == owner_id)))
    if card is None:
        card = StudentCard(tenant_id=tenant_id, student_id=student_id, uid=uid, linked_by=user.id)
        db.add(card)
    else:
        card.uid, card.linked_by = uid, user.id
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _taken(None) from exc
    await db.refresh(card)
    return card


async def unlink_card(db: AsyncSession, tenant_id: uuid.UUID, student_id: uuid.UUID) -> None:
    card = await get_card(db, tenant_id, student_id)
    if card is not None:
        await db.delete(card)
        await db.commit()


async def card_uids(
    db: AsyncSession, tenant_id: uuid.UUID, student_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    ids = list(student_ids)
    if not ids:
        return {}
    rows = await db.execute(
        select(StudentCard.student_id, StudentCard.uid).where(
            StudentCard.tenant_id == tenant_id, StudentCard.student_id.in_(ids)
        )
    )
    return dict(rows.tuples().all())
