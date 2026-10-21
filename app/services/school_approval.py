"""
Approving new schools.

A school is created `pending`. The platform team (ADMIN_NOTIFY_EMAILS) gets an email
with a one-time review link; whoever opens it can approve the school, which starts its
free trial, or reject it, which deletes it. The link's token is only stored hashed and
is cleared once used, so a link can't be replayed.

Holding the link is the authority to decide, so it only ever goes to the admin
addresses, and the frontend page asks for a click before acting (mail scanners open
links on their own).
"""

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models.tenant import Tenant
from app.db.models.users import User
from app.services.email import (
    EmailError,
    school_approved_email,
    school_rejected_email,
    send_email,
)

log = logging.getLogger(__name__)

TRIAL_DAYS = 14
PENDING, APPROVED = "pending", "approved"


class SchoolReview(BaseModel):
    id: uuid.UUID
    name: str
    country: str | None
    plan: str | None
    workspace_id: str | None
    owner_name: str | None
    owner_email: str | None
    registered_at: datetime | None


class ReviewDecision(BaseModel):
    decision: str  # "approved" | "rejected"
    school_name: str
    trial_ends_at: datetime | None = None


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_review_token() -> tuple[str, str]:
    """(token for the email link, hash to store on the school)."""
    token = secrets.token_urlsafe(32)
    return token, _hash(token)


def review_url(token: str) -> str:
    return f"{get_settings().FRONTEND_URL.rstrip('/')}/review-school?token={token}"


def _not_found() -> HTTPException:
    # Same answer for unknown, used and already-decided links.
    return HTTPException(
        status.HTTP_404_NOT_FOUND, "This review link is invalid or has already been used."
    )


async def _pending_school(
    db: AsyncSession, token: str, *, lock: bool = False
) -> tuple[Tenant, User | None]:
    query = (
        select(Tenant, User)
        .outerjoin(User, User.id == Tenant.owner_id)
        .where(Tenant.review_token_hash == _hash(token), Tenant.status == PENDING)
    )
    if lock:
        query = query.with_for_update(of=Tenant)
    row = (await db.execute(query)).first()
    if row is None:
        raise _not_found()
    return row[0], row[1]


async def preview(db: AsyncSession, token: str) -> SchoolReview:
    tenant, owner = await _pending_school(db, token)
    return SchoolReview(
        id=tenant.id,
        name=tenant.name,
        country=tenant.country,
        plan=tenant.plan,
        workspace_id=tenant.workspace_id,
        owner_name=owner.name if owner else None,
        owner_email=owner.email if owner else None,
        registered_at=tenant.created_at,
    )


async def approve(db: AsyncSession, token: str) -> tuple[ReviewDecision, User | None]:
    tenant, owner = await _pending_school(db, token, lock=True)
    now = datetime.now(UTC)
    tenant.status = APPROVED
    tenant.approved_at = now
    tenant.trial_ends_at = now + timedelta(days=TRIAL_DAYS)
    tenant.review_token_hash = None
    await db.commit()
    log.info("School %s approved", tenant.id)
    return (
        ReviewDecision(
            decision="approved", school_name=tenant.name, trial_ends_at=tenant.trial_ends_at
        ),
        owner,
    )


async def reject(db: AsyncSession, token: str) -> tuple[ReviewDecision, User | None]:
    """Deletes the school straight away (its owner keeps their account)."""
    tenant, owner = await _pending_school(db, token, lock=True)
    name, tenant_id = tenant.name, tenant.id
    await db.delete(tenant)  # memberships go with it
    await db.commit()
    log.info("School %s rejected and deleted", tenant_id)
    return ReviewDecision(decision="rejected", school_name=name), owner


async def email_owner(
    decision: ReviewDecision, owner_email: str | None, owner_name: str | None
) -> None:
    """Background task: tell the owner. A failure is logged, never raised."""
    if not owner_email:
        return
    frontend = get_settings().FRONTEND_URL.rstrip("/")
    first_name = (owner_name or "").split(" ")[0] or None
    if decision.decision == "approved" and decision.trial_ends_at:
        email = school_approved_email(
            first_name=first_name,
            school_name=decision.school_name,
            trial_ends=decision.trial_ends_at.strftime("%d %B %Y"),
            login_url=f"{frontend}/login",
        )
    else:
        email = school_rejected_email(
            first_name=first_name, school_name=decision.school_name, contact_url=frontend
        )
    subject, html_body, text_body = email
    try:
        await send_email(to=owner_email, subject=subject, html_body=html_body, text_body=text_body)
    except EmailError as exc:
        log.error("Couldn't email the school owner about the review: %s", exc)
    except Exception:
        log.exception("Couldn't email the school owner about the review")
