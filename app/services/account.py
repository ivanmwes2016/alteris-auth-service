"""
Account emails we send ourselves (through Resend) instead of letting Supabase send them:
signup confirmation, resending it, and password reset.

Supabase still owns the account, the password and the verification state. Its admin
`generate_link` creates the account (for signup) and a one-time token WITHOUT emailing
anyone; we email a link to our own /auth/confirm page, which redeems the token only
when the person clicks a button, so mail scanners that open links can't use it up.

Public responses never say whether an address has an account.
"""

import logging
from typing import Literal
from urllib.parse import quote

from fastapi import HTTPException, status
from gotrue.errors import AuthApiError
from gotrue.types import GenerateLinkParams
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from supabase import Client

from app.core.config import get_settings
from app.db.models.auth import EmailRequest, RegisterRequest
from app.helpers import rate_limit
from app.services.email import (
    Email,
    EmailError,
    account_exists_email,
    confirm_email,
    reset_password_email,
    send_email,
)

log = logging.getLogger(__name__)

HOUR = 60 * 60
CHECK_EMAIL = "Check your email for a link to confirm your account."
MAYBE_SENT_CONFIRM = "If that email has an unconfirmed account, we've sent a new confirmation link."
MAYBE_SENT_RESET = "If an account exists for that email, we've sent a link to reset the password."

ConfirmType = Literal["signup", "magiclink", "recovery"]


def _frontend(path: str) -> str:
    return f"{get_settings().FRONTEND_URL.rstrip('/')}{path}"


def confirm_url(kind: ConfirmType, token_hash: str, next_path: str) -> str:
    return _frontend(
        f"/auth/confirm?type={kind}&token_hash={quote(token_hash, safe='')}"
        f"&next={quote(next_path, safe='')}"
    )


async def _account_state(db: AsyncSession, email: str) -> bool | None:
    """None: no account. Otherwise whether its email is confirmed (Supabase's auth.users)."""
    row = (
        await db.execute(
            text(
                "select email_confirmed_at is not null from auth.users "
                "where lower(email) = :email limit 1"
            ),
            {"email": email},
        )
    ).first()
    return None if row is None else bool(row[0])


async def _token(supabase: Client, params: GenerateLinkParams) -> str:
    response = await run_in_threadpool(supabase.auth.admin.generate_link, params)
    return response.properties.hashed_token


async def _deliver(to: str, email: Email) -> None:
    subject, html_body, text_body = email
    try:
        await send_email(to=to, subject=subject, html_body=html_body, text_body=text_body)
    except EmailError as exc:
        if exc.rate_limited:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "Too many emails. Please try again shortly."
            ) from exc
        log.error("Sending an account email failed: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "We couldn't send the email. Please try again."
        ) from exc


def _supabase_unavailable(exc: Exception) -> HTTPException:
    log.error("Supabase generate_link failed: %s", exc)
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE, "Authentication service unavailable. Try again."
    )


async def _limit(scope: str, email: str, ip: str) -> None:
    await rate_limit.enforce(f"{scope}:ip", ip, limit=20, window_seconds=HOUR)
    await rate_limit.enforce(f"{scope}:email", email, limit=5, window_seconds=HOUR)


async def _send_confirmation_for_existing(supabase: Client, email: str, next_path: str) -> None:
    """An unconfirmed account: a magic link confirms the email and signs them in."""
    token = await _token(supabase, {"type": "magiclink", "email": email})
    await _deliver(
        email,
        confirm_email(confirm_url=confirm_url("magiclink", token, next_path), first_name=None),
    )


async def register(db: AsyncSession, supabase: Client, payload: RegisterRequest, ip: str) -> str:
    await _limit("verify", payload.email, ip)
    next_path = payload.next or "/get-started"

    try:
        state = await _account_state(db, payload.email)
        if state is True:
            # Same response as a new signup, so this can't be used to find accounts.
            await _deliver(payload.email, account_exists_email(login_url=_frontend("/login")))
            return CHECK_EMAIL
        if state is False:
            # Never change an unconfirmed account's password here: whoever set it first
            # might not be the owner. Re-send the confirmation instead.
            await _send_confirmation_for_existing(supabase, payload.email, next_path)
            return CHECK_EMAIL

        token = await _token(
            supabase,
            {
                "type": "signup",
                "email": payload.email,
                "password": payload.password,
                "options": {
                    "data": {"first_name": payload.first_name, "last_name": payload.last_name}
                },
            },
        )
    except AuthApiError as exc:
        if exc.code in {"email_exists", "user_already_exists"}:
            await _deliver(payload.email, account_exists_email(login_url=_frontend("/login")))
            return CHECK_EMAIL
        if exc.code == "weak_password":
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "Choose a stronger password."
            ) from exc
        if exc.status < 500:
            log.warning("Supabase rejected a signup: status=%s code=%s", exc.status, exc.code)
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "We couldn't create an account with those details."
            ) from exc
        raise _supabase_unavailable(exc) from exc

    await _deliver(
        payload.email,
        confirm_email(
            confirm_url=confirm_url("signup", token, next_path), first_name=payload.first_name
        ),
    )
    return CHECK_EMAIL


async def resend_verification(
    db: AsyncSession, supabase: Client, payload: EmailRequest, ip: str
) -> str:
    await _limit("verify", payload.email, ip)
    # Failures are logged, not returned: they'd only ever happen for real accounts.
    try:
        if await _account_state(db, payload.email) is False:
            await _send_confirmation_for_existing(
                supabase, payload.email, payload.next or "/get-started"
            )
    except (AuthApiError, HTTPException) as exc:
        log.error("Resending a confirmation failed: %s", exc)
    return MAYBE_SENT_CONFIRM


async def forgot_password(
    db: AsyncSession, supabase: Client, payload: EmailRequest, ip: str
) -> str:
    await _limit("reset", payload.email, ip)
    try:
        if await _account_state(db, payload.email) is not None:
            token = await _token(supabase, {"type": "recovery", "email": payload.email})
            await _deliver(
                payload.email,
                reset_password_email(reset_url=confirm_url("recovery", token, "/reset-password")),
            )
    except (AuthApiError, HTTPException) as exc:
        # Logged, not returned: an error here would reveal that the account exists.
        log.error("Sending a password reset failed: %s", exc)
    return MAYBE_SENT_RESET
