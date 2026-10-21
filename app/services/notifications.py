"""
Alerts to the platform team (not to schools). Run as background tasks after the
request has succeeded, and never raise: a failed alert is logged, not fatal.
"""

import logging
from datetime import UTC, datetime

from app.core.config import get_settings
from app.services.email import (
    EmailError,
    school_received_email,
    school_registered_email,
    send_email,
)

log = logging.getLogger(__name__)


def _recipients() -> list[str]:
    raw = get_settings().ADMIN_NOTIFY_EMAILS or ""
    return [address.strip() for address in raw.split(",") if address.strip()]


async def notify_school_registered(
    *,
    school_name: str,
    country: str | None,
    plan: str | None,
    workspace_id: str | None,
    owner_name: str | None,
    owner_email: str,
    review_url: str,
) -> None:
    recipients = _recipients()
    if not recipients:
        return

    owner = f"{owner_name} <{owner_email}>" if owner_name else owner_email
    subject, html_body, text_body = school_registered_email(
        school_name=school_name,
        country=country,
        plan=plan,
        workspace_id=workspace_id,
        owner=owner,
        registered_at=datetime.now(UTC).strftime("%d %b %Y, %H:%M UTC"),
        review_url=review_url,
    )
    for address in recipients:
        try:
            await send_email(to=address, subject=subject, html_body=html_body, text_body=text_body)
        except EmailError as exc:
            log.error("New-school alert to an admin address failed: %s", exc)
        except Exception:
            log.exception("New-school alert failed")


async def notify_owner_school_received(
    *, school_name: str, owner_name: str | None, owner_email: str
) -> None:
    """Thank the person who registered and tell them the school is pending approval."""
    first_name = (owner_name or "").split(" ")[0] or None
    subject, html_body, text_body = school_received_email(
        first_name=first_name, school_name=school_name, app_url=get_settings().FRONTEND_URL
    )
    try:
        await send_email(to=owner_email, subject=subject, html_body=html_body, text_body=text_body)
    except EmailError as exc:
        log.error("Couldn't send the school-received email to its owner: %s", exc)
    except Exception:
        log.exception("Couldn't send the school-received email to its owner")
