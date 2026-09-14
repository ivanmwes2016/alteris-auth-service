"""
Service layer for the academic term settings singleton — one row per tenant.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.academic_term import AcademicTermSettings
from app.db.schemas.academic_term import TermSettingsUpdate


async def get_settings(db: AsyncSession, *, tenant_id: uuid.UUID) -> AcademicTermSettings | None:
    result = await db.execute(
        select(AcademicTermSettings).where(AcademicTermSettings.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


async def get_or_create_settings(db: AsyncSession, *, tenant_id: uuid.UUID) -> AcademicTermSettings:
    """
    Atomically get or create the tenant's term settings row.

    PostgreSQL ON CONFLICT prevents a check-then-insert race when two
    requests attempt to create the same tenant's row concurrently.
    """
    settings = await get_settings(db, tenant_id=tenant_id)

    if settings is not None:
        return settings

    insert_stmt = (
        pg_insert(AcademicTermSettings)
        .values(tenant_id=tenant_id, labels=[])
        .on_conflict_do_nothing(index_elements=["tenant_id"])
    )

    await db.execute(insert_stmt)
    await db.flush()

    settings = await get_settings(db, tenant_id=tenant_id)

    if settings is None:
        raise RuntimeError(f"Failed to get or create term settings for tenant_id={tenant_id}")

    return settings


async def update_settings(
    db: AsyncSession, *, tenant_id: uuid.UUID, data: TermSettingsUpdate
) -> AcademicTermSettings:
    """Always overwrites all three fields — the frontend sends the whole object on every save."""
    settings = await get_or_create_settings(db, tenant_id=tenant_id)

    settings.labels = data.labels
    settings.current_label = data.currentLabel
    settings.current_year = data.currentYear

    await db.commit()
    await db.refresh(settings)

    return settings
