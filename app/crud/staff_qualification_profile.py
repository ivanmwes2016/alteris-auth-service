"""
Service layer for staff qualification records.

Uses one bulk-sync operation on the qualification profile instead of separate
create/update/delete endpoints for each record.

Every operation is scoped by tenant_id. Records are only updated when their
IDs already belong to the current staff member's qualification profile.
"""

import uuid
from collections.abc import Iterable, Sequence
from typing import TypeVar

from pydantic import BaseModel
from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.staff import StaffQualification, StaffQualificationProfile
from app.db.schemas.staff_qualification_profile import QualificationProfileBulkUpdate

ModelT = TypeVar("ModelT", bound=StaffQualification)


def _profile_query(
    *,
    staff_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> Select[tuple[StaffQualificationProfile]]:
    """
    Build the standard qualification-profile query.

    Relationships are eagerly loaded because AsyncSession does not support
    implicit lazy-loading through normal attribute access.
    """
    return (
        select(StaffQualificationProfile)
        .where(
            StaffQualificationProfile.staff_id == staff_id,
            StaffQualificationProfile.tenant_id == tenant_id,
        )
        .options(selectinload(StaffQualificationProfile.qualifications))
    )


async def get_profile(
    db: AsyncSession,
    *,
    staff_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> StaffQualificationProfile | None:
    """
    Return a staff member's qualification profile, including all records.

    The query is always tenant-scoped.
    """
    stmt = _profile_query(staff_id=staff_id, tenant_id=tenant_id)

    result = await db.execute(stmt)

    return result.scalar_one_or_none()


async def get_or_create_profile(
    db: AsyncSession,
    *,
    staff_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> StaffQualificationProfile:
    """
    Atomically get or create a qualification profile.

    PostgreSQL ON CONFLICT prevents a check-then-insert race when two
    requests attempt to create the same staff member's profile concurrently.

    The profile is re-selected after the insert so all relationships are
    eagerly loaded before it is returned.
    """
    profile = await get_profile(db, staff_id=staff_id, tenant_id=tenant_id)

    if profile is not None:
        return profile

    insert_stmt = (
        pg_insert(StaffQualificationProfile)
        .values(
            staff_id=staff_id,
            tenant_id=tenant_id,
        )
        .on_conflict_do_nothing(
            index_elements=[
                "staff_id",
            ]
        )
    )

    await db.execute(insert_stmt)
    await db.flush()

    profile = await get_profile(db, staff_id=staff_id, tenant_id=tenant_id)

    if profile is None:
        raise RuntimeError(f"Failed to get or create qualification profile for staff_id={staff_id}")

    return profile


async def _sync_qualifications(
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
    staff_id: uuid.UUID,
    tenant_id: uuid.UUID,
    data: QualificationProfileBulkUpdate,
) -> StaffQualificationProfile:
    """
    Synchronise a staff member's complete qualification profile.

    The scalar `notes` field is updated first, followed by `qualifications`
    if present in the request.

    `qualifications` set to None is left unchanged. `qualifications`
    explicitly supplied as an empty list removes all existing records.
    """
    profile = await get_or_create_profile(db, staff_id=staff_id, tenant_id=tenant_id)

    scalar_fields = data.model_dump(
        exclude_unset=True,
        exclude={"qualifications"},
    )

    for field, value in scalar_fields.items():
        setattr(profile, field, value)

    if data.qualifications is not None:
        await _sync_qualifications(
            db,
            existing=profile.qualifications,
            incoming=data.qualifications,
            model_cls=StaffQualification,
            profile_id=profile.id,
            tenant_id=tenant_id,
        )

    await db.commit()

    # Re-query instead of relying on lazy loading or refresh() to populate
    # relationship collections after the commit.
    refreshed_profile = await get_profile(db, staff_id=staff_id, tenant_id=tenant_id)

    if refreshed_profile is None:
        raise RuntimeError(
            f"Qualification profile disappeared after synchronisation for staff_id={staff_id}"
        )

    return refreshed_profile
