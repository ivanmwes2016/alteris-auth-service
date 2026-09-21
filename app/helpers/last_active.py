import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, update

from app.core.db import SessionLocal
from app.db.models.tenant_member import TenantMember

log = logging.getLogger(__name__)

THROTTLE = timedelta(minutes=15)


async def touch_last_active(user_id: uuid.UUID) -> None:
    """
    Stamp last_active_at at most once per THROTTLE window.

    Runs as a background task in its own session, so it can't affect the
    request that triggered it, and never raises.
    """
    cutoff = datetime.now(UTC) - THROTTLE
    try:
        async with SessionLocal() as session:
            await session.execute(
                update(TenantMember)
                .where(
                    TenantMember.user_id == user_id,
                    TenantMember.status == "active",
                    or_(
                        TenantMember.last_active_at.is_(None),
                        TenantMember.last_active_at < cutoff,
                    ),
                )
                .values(last_active_at=func.now())
            )
            await session.commit()
    except Exception:
        log.exception("Failed to record last activity for user %s", user_id)
