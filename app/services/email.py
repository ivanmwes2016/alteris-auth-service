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


Email = tuple[str, str, str]  # (subject, html, text)

_BUTTON = (
    "display:inline-block;padding:12px 24px;border-radius:999px;"
    "background:#1e293b;color:#ffffff;text-decoration:none"
)


def _render(
    *, subject: str, first_name: str | None, lines: list[str], action: str, url: str, note: str
) -> Email:
    """One layout for every email: greeting, lines, a button, and a small print note."""
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    text = "\n\n".join([greeting, *lines, f"{action}: {url}", note])
    e = html.escape
    body = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
        'line-height:1.5;color:#1e293b">'
        f"<p>{e(greeting)}</p>"
        + "".join(f"<p>{e(line)}</p>" for line in lines)
        + f'<p><a href="{e(url)}" style="{_BUTTON}">{e(action)}</a></p>'
        f'<p style="font-size:13px;color:#64748b">{e(note)}</p>'
        "</div>"
    )
    return subject, body, text


def invite_email(
    *, invite_url: str, workspace_name: str, role_label: str, first_name: str | None
) -> Email:
    """An invitation. The link only opens our invite page; it signs no one in."""
    return _render(
        subject=f"You're invited to join {workspace_name}",
        first_name=first_name,
        lines=[f"You've been invited to join {workspace_name} as {role_label}."],
        action="Accept the invitation",
        url=invite_url,
        note="You'll sign in, or create an account with this email address, to accept. "
        "The link expires in 7 days. If you weren't expecting this, you can ignore it.",
    )


def confirm_email(*, confirm_url: str, first_name: str | None) -> Email:
    return _render(
        subject="Confirm your email",
        first_name=first_name,
        lines=["Confirm your email address to finish creating your account."],
        action="Confirm my email",
        url=confirm_url,
        note="If you didn't create an account, you can ignore this email.",
    )


def account_exists_email(*, login_url: str) -> Email:
    """Sent instead of a confirmation when someone signs up with a registered email."""
    return _render(
        subject="You already have an account",
        first_name=None,
        lines=[
            "Someone, hopefully you, tried to create an account with this email address. "
            "You already have one, so no new account was made.",
            'Forgot your password? Use "Forgot password" on the sign-in page.',
        ],
        action="Sign in",
        url=login_url,
        note="If this wasn't you, you can ignore this email. Your account hasn't changed.",
    )


def reset_password_email(*, reset_url: str) -> Email:
    return _render(
        subject="Reset your password",
        first_name=None,
        lines=["We received a request to reset your password."],
        action="Choose a new password",
        url=reset_url,
        note="If you didn't ask for this, you can ignore this email. "
        "Your password stays the same until you choose a new one.",
    )
