"""
Service layer for student medical records — "Option B": one bulk sync per profile
instead of separate create/update/delete endpoints per child resource.

Every query is scoped by tenant_id — never trust a bare id from the payload
without it, or one tenant could edit another's rows by id-guessing.
"""

import uuid
from collections.abc import Iterable, Sequence
from typing import TypeVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import StudentMedicalProfile
from app.db.models.medical_note import (
    StudentAllergy,
    StudentMedicalCondition,
    StudentMedication,
)
from app.db.schemas.medical_profile import MedicalProfileBulkUpdate

ModelT = TypeVar(
    "ModelT",
    StudentAllergy,
    StudentMedication,
    StudentMedicalCondition,
)


async def get_or_create_profile(
    db: AsyncSession, *, student_id: uuid.UUID, tenant_id: uuid.UUID
) -> StudentMedicalProfile:
    stmt = select(StudentMedicalProfile).where(
        StudentMedicalProfile.student_id == student_id,
        StudentMedicalProfile.tenant_id == tenant_id,
    )
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if profile is None:
        profile = StudentMedicalProfile(student_id=student_id, tenant_id=tenant_id)
        db.add(profile)
        await db.flush()  # assigns profile.id without committing yet
    return profile


async def _sync_children(
    db: AsyncSession,
    *,
    existing: Iterable[ModelT],
    incoming: Sequence[BaseModel],
    model_cls: type[ModelT],
    profile_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> None:
    """
    Diff one child list against the incoming payload:
      - incoming item with a matching existing id  -> update in place
      - incoming item with no id / unmatched id     -> insert new row
      - existing row not present in incoming        -> delete
    """
    existing_by_id = {e.id: e for e in existing}
    seen_ids: set[uuid.UUID] = set()

    for item in incoming:
        data = item.model_dump(exclude={"id"})
        item_id = getattr(item, "id", None)

        if item_id and item_id in existing_by_id:
            row = existing_by_id[item_id]
            for field, value in data.items():
                setattr(row, field, value)
            seen_ids.add(item_id)
        else:
            row = model_cls(profile_id=profile_id, tenant_id=tenant_id, **data)
            db.add(row)

    for existing_id, row in existing_by_id.items():
        if existing_id not in seen_ids:
            await db.delete(row)


async def sync_profile(
    db: AsyncSession,
    *,
    student_id: uuid.UUID,
    tenant_id: uuid.UUID,
    data: MedicalProfileBulkUpdate,
) -> StudentMedicalProfile:
    profile = await get_or_create_profile(db, student_id=student_id, tenant_id=tenant_id)

    scalar_fields = data.model_dump(
        exclude_unset=True, exclude={"allergies", "medications", "conditions"}
    )
    for field, value in scalar_fields.items():
        setattr(profile, field, value)

    if data.allergies is not None:
        await _sync_children(
            db,
            existing=list(profile.allergies),
            incoming=data.allergies,
            model_cls=StudentAllergy,
            profile_id=profile.id,
            tenant_id=tenant_id,
        )

    if data.medications is not None:
        await _sync_children(
            db,
            existing=list(profile.medications),
            incoming=data.medications,
            model_cls=StudentMedication,
            profile_id=profile.id,
            tenant_id=tenant_id,
        )

    if data.conditions is not None:
        await _sync_children(
            db,
            existing=list(profile.conditions),
            incoming=data.conditions,
            model_cls=StudentMedicalCondition,
            profile_id=profile.id,
            tenant_id=tenant_id,
        )

    await db.commit()
    await db.refresh(profile)
    return profile


async def get_profile(
    db: AsyncSession, *, student_id: uuid.UUID, tenant_id: uuid.UUID
) -> StudentMedicalProfile | None:
    stmt = select(StudentMedicalProfile).where(
        StudentMedicalProfile.student_id == student_id,
        StudentMedicalProfile.tenant_id == tenant_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()
