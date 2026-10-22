"""
Email the platform team a fresh review link for every school still waiting for approval.

For schools whose first alert never went out (e.g. ADMIN_NOTIFY_EMAILS wasn't set when
they registered). Each school gets a new one-time link, so any older link for it stops
working. Uses the database, Resend key and ADMIN_NOTIFY_EMAILS from the environment
(.env); FRONTEND_URL must be the live site, since the links open it.

    uv run python -m scripts.resend_school_reviews            # list pending schools only
    FRONTEND_URL=https://app.skolarly.net uv run python -m scripts.resend_school_reviews --send
"""

import argparse
import asyncio
import sys
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import SessionLocal, engine
from app.db.models.tenant import Tenant
from app.db.models.users import User
from app.services.notifications import notify_school_registered
from app.services.school_approval import PENDING, new_review_token, review_url


async def pending_schools(db: AsyncSession) -> list[tuple[Tenant, User | None]]:
    rows = await db.execute(
        select(Tenant, User)
        .outerjoin(User, User.id == Tenant.owner_id)
        .where(Tenant.status == PENDING)
        .order_by(Tenant.created_at)
    )
    return [(tenant, owner) for tenant, owner in rows.all()]


def problems() -> list[str]:
    """Reasons sending would be pointless: nobody to email, or links that don't open."""
    settings = get_settings()
    found = []
    if not (settings.ADMIN_NOTIFY_EMAILS or "").strip():
        found.append("ADMIN_NOTIFY_EMAILS is not set, so nobody would get the links")
    if not settings.RESEND_API_KEY or not settings.EMAIL_FROM:
        found.append("RESEND_API_KEY / EMAIL_FROM are not set")
    frontend = settings.FRONTEND_URL or ""
    if urlparse(frontend).scheme != "https" or urlparse(frontend).hostname in {
        "localhost",
        "127.0.0.1",
    }:
        found.append(f"FRONTEND_URL is {frontend!r}; the links must open the live site")
    return found


async def resend(db: AsyncSession, schools: list[tuple[Tenant, User | None]]) -> None:
    for tenant, owner in schools:
        token, token_hash = new_review_token()
        tenant.review_token_hash = token_hash
        await db.commit()  # the link has to work before anyone can click it
        await notify_school_registered(
            school_name=tenant.name,
            country=tenant.country,
            plan=tenant.plan,
            workspace_id=tenant.workspace_id,
            owner_name=owner.name if owner else None,
            owner_email=owner.email if owner else "(no owner)",
            phone=tenant.phone,
            school_type=tenant.school_type,
            referral_source=tenant.referral_source,
            review_url=review_url(token),
        )
        print(f"  sent: {tenant.name}")


async def run(*, send: bool) -> int:
    engine.sync_engine.echo = False  # the app logs every SQL statement; too noisy here
    async with SessionLocal() as db:
        schools = await pending_schools(db)
        print(f"{len(schools)} school(s) waiting for approval:")
        for tenant, owner in schools:
            registered = f"{tenant.created_at:%d %b %Y %H:%M}" if tenant.created_at else "?"
            print(f"  {registered}  {tenant.name}  ({owner.email if owner else 'no owner'})")
        if not send or not schools:
            return 0

        if found := problems():
            print("Not sending:", *found, sep="\n  ", file=sys.stderr)
            return 1
        print("Emailing review links (a failed email is logged below):")
        await resend(db, schools)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-send review links for pending schools.")
    parser.add_argument("--send", action="store_true", help="email the links (default: list)")
    sys.exit(asyncio.run(run(send=parser.parse_args().send)))


if __name__ == "__main__":
    main()
