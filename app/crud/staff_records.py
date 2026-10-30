"""
A staff member's documents and health record, each saved whole from its tab on
the staff profile (app.db.schemas.staff_records). Every query is scoped by
tenant_id; the routes check the caller's role and the staff member's school
first (app.crud.staff_access).
"""

import uuid
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.staff import (
    StaffAllergy,
    StaffDocument,
    StaffHealthCondition,
    StaffHealthProfile,
)
from app.db.schemas.staff_records import (
    StaffDocumentsRead,
    StaffDocumentsWrite,
    StaffHealthRead,
    StaffHealthWrite,
)

RowT = TypeVar("RowT", StaffDocument, StaffHealthCondition, StaffAllergy)


async def sync_rows(
    db: AsyncSession,
    *,
    existing: Sequence[RowT],
    incoming: Sequence[BaseModel],
    new_row: Callable[[dict[str, Any]], RowT],
) -> None:
    """
    Make the saved rows match the list sent: an item whose id matches a row
    updates it, any other item is added (with an id of our own, since the
    School Hub makes one up for new items), and rows left out are deleted.
    """
    by_id = {row.id: row for row in existing}
    kept: set[uuid.UUID] = set()
    for item in incoming:
        item_id = getattr(item, "id", None)
        data = item.model_dump(exclude={"id"})
        row = by_id.get(item_id) if item_id is not None else None
        if row is None or row.id in kept:
            db.add(new_row(data))
            continue
        for field, value in data.items():
            setattr(row, field, value)
        kept.add(row.id)
    for row_id, row in by_id.items():
        if row_id not in kept:
            await db.delete(row)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
async def _documents(
    db: AsyncSession, *, staff_id: uuid.UUID, tenant_id: uuid.UUID
) -> Sequence[StaffDocument]:
    result = await db.scalars(
        select(StaffDocument)
        .where(StaffDocument.staff_id == staff_id, StaffDocument.tenant_id == tenant_id)
        .order_by(StaffDocument.created_at, StaffDocument.id)
        .execution_options(populate_existing=True)
    )
    return result.all()


async def get_documents(
    db: AsyncSession, *, staff_id: uuid.UUID, tenant_id: uuid.UUID
) -> StaffDocumentsRead:
    rows = await _documents(db, staff_id=staff_id, tenant_id=tenant_id)
    return StaffDocumentsRead.model_validate({"documents": rows})


async def save_documents(
    db: AsyncSession, *, staff_id: uuid.UUID, tenant_id: uuid.UUID, data: StaffDocumentsWrite
) -> StaffDocumentsRead:
    await sync_rows(
        db,
        existing=await _documents(db, staff_id=staff_id, tenant_id=tenant_id),
        incoming=data.documents,
        new_row=lambda d: StaffDocument(staff_id=staff_id, tenant_id=tenant_id, **d),
    )
    await db.commit()
    return await get_documents(db, staff_id=staff_id, tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
async def _health_profile(
    db: AsyncSession, *, staff_id: uuid.UUID, tenant_id: uuid.UUID
) -> StaffHealthProfile | None:
    result = await db.scalars(
        select(StaffHealthProfile)
        .where(StaffHealthProfile.staff_id == staff_id, StaffHealthProfile.tenant_id == tenant_id)
        .execution_options(populate_existing=True)
    )
    return result.one_or_none()


async def get_health(
    db: AsyncSession, *, staff_id: uuid.UUID, tenant_id: uuid.UUID
) -> StaffHealthRead:
    """Nothing saved yet reads as an empty record; reading never creates one."""
    profile = await _health_profile(db, staff_id=staff_id, tenant_id=tenant_id)
    return StaffHealthRead.model_validate(profile) if profile else StaffHealthRead()


async def save_health(
    db: AsyncSession, *, staff_id: uuid.UUID, tenant_id: uuid.UUID, data: StaffHealthWrite
) -> StaffHealthRead:
    # ON CONFLICT: two first saves at once make one record, not an error.
    await db.execute(
        pg_insert(StaffHealthProfile)
        .values(staff_id=staff_id, tenant_id=tenant_id)
        .on_conflict_do_nothing(index_elements=["staff_id"])
    )
    profile = await _health_profile(db, staff_id=staff_id, tenant_id=tenant_id)
    if profile is None:
        raise RuntimeError(f"Failed to get or create the health record for staff_id={staff_id}")

    for field, value in data.model_dump(
        exclude_unset=True, exclude={"conditions", "allergies"}
    ).items():
        setattr(profile, field, value)
    if data.conditions is not None:
        await sync_rows(
            db,
            existing=profile.conditions,
            incoming=data.conditions,
            new_row=lambda d: StaffHealthCondition(profile_id=profile.id, tenant_id=tenant_id, **d),
        )
    if data.allergies is not None:
        await sync_rows(
            db,
            existing=profile.allergies,
            incoming=data.allergies,
            new_row=lambda d: StaffAllergy(profile_id=profile.id, tenant_id=tenant_id, **d),
        )
    await db.commit()
    return await get_health(db, staff_id=staff_id, tenant_id=tenant_id)
