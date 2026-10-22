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


def school_registered_email(
    *,
    school_name: str,
    country: str | None,
    plan: str | None,
    workspace_id: str | None,
    owner: str,
    phone: str | None,
    school_type: str | None,
    referral_source: str | None,
    registered_at: str,
    review_url: str,
) -> Email:
    """Internal alert to the platform team: a new school is waiting for approval."""
    return _render(
        subject=f"New school awaiting approval: {school_name}",
        first_name=None,
        lines=[
            f"{school_name} has just registered and is waiting for approval.",
            f"Owner: {owner}",
            f"Phone: {phone or 'not given'}",
            f"Type: {school_type or 'not given'} · Country: {country or 'not given'} · "
            f"Plan: {plan or 'not given'}",
            f"Heard about us from: {referral_source or 'not given'}",
            f"Workspace: {workspace_id or 'not given'}",
            f"Registered: {registered_at}",
            "Nobody can use the school until it's approved. Rejecting deletes it.",
        ],
        action="Review this school",
        url=review_url,
        note="You get this because your address is in ADMIN_NOTIFY_EMAILS. "
        "The review link works once; don't forward it.",
    )


def school_approved_email(
    *, first_name: str | None, school_name: str, trial_ends: str, login_url: str
) -> Email:
    return _render(
        subject=f"{school_name} is approved",
        first_name=first_name,
        lines=[
            f"Good news: {school_name} has been approved and is ready to use.",
            f"Your free trial runs until {trial_ends}.",
        ],
        action="Sign in",
        url=login_url,
        note="Questions? Just reply to this email.",
    )


def school_rejected_email(*, first_name: str | None, school_name: str, contact_url: str) -> Email:
    return _render(
        subject=f"About your registration for {school_name}",
        first_name=first_name,
        lines=[
            f"Thank you for registering {school_name}. "
            "We weren't able to approve it, so the registration has been removed.",
            "If you think this is a mistake, please get in touch.",
        ],
        action="Visit Skolarly",
        url=contact_url,
        note="Your account itself hasn't been deleted.",
    )


def school_received_email(*, first_name: str | None, school_name: str, app_url: str) -> Email:
    """To the person who registered a school: thanks, it's pending, we'll be in touch."""
    return _render(
        subject=f"Thanks for registering {school_name} with Skolarly",
        first_name=first_name,
        lines=[
            "Thank you for trying out Skolarly!",
            f"We've received your application for {school_name}. It's now pending review "
            "by our team.",
            "We'll email you as soon as your school has been approved. Your 14-day free "
            "trial starts from that moment, so you won't lose any of it while you wait.",
        ],
        action="Visit Skolarly",
        url=app_url,
        note="You're receiving this because you registered a school on Skolarly.",
    )
