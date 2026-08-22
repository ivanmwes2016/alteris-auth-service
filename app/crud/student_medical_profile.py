"""
Service layer for student medical records.

Uses one bulk-sync operation per medical profile instead of separate
create/update/delete endpoints for each child resource.

Every operation is scoped by tenant_id. Child records are only updated when
their IDs already belong to the current student's medical profile.
"""

import uuid
from collections.abc import Iterable, Sequence
from typing import TypeVar

from pydantic import BaseModel
from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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


def _profile_query(
    *,
    student_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> Select[tuple[StudentMedicalProfile]]:
    """
    Build the standard medical-profile query.

    Relationships are eagerly loaded because AsyncSession does not support
    implicit lazy-loading through normal attribute access.
    """
    return (
        select(StudentMedicalProfile)
        .where(
            StudentMedicalProfile.student_id == student_id,
            StudentMedicalProfile.tenant_id == tenant_id,
        )
        .options(
            selectinload(StudentMedicalProfile.allergies),
            selectinload(StudentMedicalProfile.medications),
            selectinload(StudentMedicalProfile.conditions),
        )
    )


async def get_profile(
    db: AsyncSession,
    *,
    student_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> StudentMedicalProfile | None:
    """
    Return a student's medical profile, including all child collections.

    The query is always tenant-scoped.
    """
    stmt = _profile_query(
        student_id=student_id,
        tenant_id=tenant_id,
    )

    result = await db.execute(stmt)

    return result.scalar_one_or_none()


async def get_or_create_profile(
    db: AsyncSession,
    *,
    student_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> StudentMedicalProfile:
    """
    Atomically get or create a medical profile.

    PostgreSQL ON CONFLICT prevents a check-then-insert race when two
    requests attempt to create the same student's profile concurrently.

    The profile is re-selected after the insert so all relationships are
    eagerly loaded before it is returned.
    """
    profile = await get_profile(
        db,
        student_id=student_id,
        tenant_id=tenant_id,
    )

    if profile is not None:
        return profile

    insert_stmt = (
        pg_insert(StudentMedicalProfile)
        .values(
            student_id=student_id,
            tenant_id=tenant_id,
        )
        .on_conflict_do_nothing(
            index_elements=[
                "student_id",
            ]
        )
    )

    await db.execute(insert_stmt)
    await db.flush()

    profile = await get_profile(
        db,
        student_id=student_id,
        tenant_id=tenant_id,
    )

    if profile is None:
        raise RuntimeError(f"Failed to get or create medical profile for student_id={student_id}")

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
    existing_by_id = {row.id: row for row in existing}
    seen_ids: set[uuid.UUID] = set()

    for item in incoming:
        item_id = getattr(item, "id", None)

        data = item.model_dump(
            exclude={"id", "profile_id", "tenant_id"},
            exclude_unset=True,
        )

        # New item
        if item_id is None:
            db.add(
                model_cls(
                    profile_id=profile_id,
                    tenant_id=tenant_id,
                    **data,
                )
            )
            continue

        # Existing item belonging to this profile
        existing_row = existing_by_id.get(item_id)

        if existing_row is not None:
            for field, value in data.items():
                setattr(existing_row, field, value)

            seen_ids.add(item_id)
            continue

        # ID supplied, but it isn't part of this profile.
        # Treat it as a new row if your frontend may send temporary/stale IDs.
        db.add(
            model_cls(
                profile_id=profile_id,
                tenant_id=tenant_id,
                **data,
            )
        )

    # Anything that existed before but wasn't sent back gets deleted.
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
    """
    Synchronise a student's complete medical profile.

    Scalar profile fields are updated first, followed by any child collections
    present in the request.

    A child collection set to None is left unchanged.

    A child collection explicitly supplied as an empty list removes all
    existing records in that collection.
    """
    profile = await get_or_create_profile(
        db,
        student_id=student_id,
        tenant_id=tenant_id,
    )

    scalar_fields = data.model_dump(
        exclude_unset=True,
        exclude={
            "allergies",
            "medications",
            "conditions",
        },
    )

    for field, value in scalar_fields.items():
        setattr(profile, field, value)

    if data.allergies is not None:
        await _sync_children(
            db,
            existing=profile.allergies,
            incoming=data.allergies,
            model_cls=StudentAllergy,
            profile_id=profile.id,
            tenant_id=tenant_id,
        )

    if data.medications is not None:
        await _sync_children(
            db,
            existing=profile.medications,
            incoming=data.medications,
            model_cls=StudentMedication,
            profile_id=profile.id,
            tenant_id=tenant_id,
        )

    if data.conditions is not None:
        await _sync_children(
            db,
            existing=profile.conditions,
            incoming=data.conditions,
            model_cls=StudentMedicalCondition,
            profile_id=profile.id,
            tenant_id=tenant_id,
        )

    await db.commit()

    # Re-query instead of relying on lazy loading or refresh() to populate
    # relationship collections after the commit.
    refreshed_profile = await get_profile(
        db,
        student_id=student_id,
        tenant_id=tenant_id,
    )

    if refreshed_profile is None:
        raise RuntimeError(
            f"Medical profile disappeared after synchronisation for student_id={student_id}"
        )

    return refreshed_profile
