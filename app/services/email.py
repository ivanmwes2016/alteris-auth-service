"""
Transactional email through Resend (https://resend.com/docs/api-reference/emails/send-email).

Only for our own emails (invitations). Signup verification and password reset are
still sent by Supabase Auth.
"""

import html
import logging

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"


class EmailError(Exception):
    """The email wasn't sent. `rate_limited` lets callers ask the user to wait."""

    def __init__(self, message: str, *, rate_limited: bool = False) -> None:
        super().__init__(message)
        self.rate_limited = rate_limited


async def send_email(*, to: str, subject: str, html_body: str, text_body: str) -> None:
    settings = get_settings()
    if not settings.RESEND_API_KEY or not settings.EMAIL_FROM:
        raise EmailError("RESEND_API_KEY / EMAIL_FROM are not configured")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                RESEND_URL,
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
                json={
                    "from": settings.EMAIL_FROM,
                    "to": [to],
                    "subject": subject,
                    "html": html_body,
                    "text": text_body,
                },
            )
    except httpx.HTTPError as exc:
        raise EmailError(f"Resend unreachable: {type(exc).__name__}") from exc

    if response.status_code == 429:
        raise EmailError("Resend rate limit", rate_limited=True)
    if response.is_error:
        # Resend's error body names the problem (e.g. unverified domain); it holds no secrets.
        raise EmailError(f"Resend rejected the email: {response.status_code} {response.text[:300]}")


def invite_email(
    *, invite_url: str, workspace_name: str, role_label: str, first_name: str | None
) -> tuple[str, str, str]:
    """(subject, html, text) for an invitation. The link only opens our invite page."""
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    subject = f"You're invited to join {workspace_name}"
    text = (
        f"{greeting}\n\n"
        f"You've been invited to join {workspace_name} as {role_label}.\n\n"
        f"Accept the invitation: {invite_url}\n\n"
        "You'll sign in, or create an account with this email address, to accept. "
        "The link expires in 7 days. If you weren't expecting this, you can ignore it."
    )
    e = html.escape
    button = (
        "display:inline-block;padding:12px 24px;border-radius:999px;"
        "background:#1e293b;color:#ffffff;text-decoration:none"
    )
    body = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
        'line-height:1.5;color:#1e293b">'
        f"<p>{e(greeting)}</p>"
        f"<p>You've been invited to join <strong>{e(workspace_name)}</strong> "
        f"as {e(role_label)}.</p>"
        f'<p><a href="{e(invite_url)}" style="{button}">Accept invitation</a></p>'
        '<p style="font-size:13px;color:#64748b">'
        "You'll sign in, or create an account with this email address, to accept. "
        "The link expires in 7 days. If you weren't expecting this, you can ignore it.</p>"
        "</div>"
    )
    return subject, body, text
