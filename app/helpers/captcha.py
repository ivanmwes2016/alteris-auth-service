"""
Cloudflare Turnstile: proof that a public form was filled in by a person, checked here
on the server (a token the browser merely claims to have is worth nothing).

Enforced only once TURNSTILE_SECRET_KEY is set, so the backend can ship before the
frontend sends tokens, and unsetting the key switches the check off again.

If Cloudflare can't be reached the request is refused (503): the API itself sits behind
Cloudflare, so an outage there takes the site down anyway, and letting requests through
would hand bots a way around the check.
"""

import logging
import uuid

import httpx
from fastapi import HTTPException, status

from app.core.config import get_settings
from app.helpers.rate_limit import log_throttled

log = logging.getLogger(__name__)

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
MAX_TOKEN_LENGTH = 2048  # Cloudflare's limit
ATTEMPTS = 2  # one retry, for network errors and Cloudflare's own internal-error

FAILED = "We couldn't confirm you're human. Please try again."
UNAVAILABLE = "Human verification is unavailable right now. Please try again shortly."


class SiteverifyUnavailableError(Exception):
    pass


async def _siteverify(secret: str, token: str, idempotency_key: str) -> dict[str, object]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
        response = await client.post(
            SITEVERIFY_URL,
            data={"secret": secret, "response": token, "idempotency_key": idempotency_key},
        )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise ValueError("siteverify returned a non-object")
    return result


async def _check(secret: str, token: str) -> list[str]:
    """Cloudflare's error codes for `token`; empty when it's valid."""
    # Retries reuse the key, so Cloudflare doesn't count a retried token as spent.
    idempotency_key = str(uuid.uuid4())
    reason = ""
    for _ in range(ATTEMPTS):
        try:
            result = await _siteverify(secret, token, idempotency_key)
        except (httpx.HTTPError, ValueError) as exc:
            reason = type(exc).__name__
            continue
        raw_codes = result.get("error-codes")
        codes = [str(code) for code in raw_codes] if isinstance(raw_codes, list) else []
        if result.get("success") is True:
            return []
        if "internal-error" not in codes:
            return codes or ["unknown"]
        reason = "internal-error"
    raise SiteverifyUnavailableError(reason)


async def verify(token: str | None, *, form: str, ip: str) -> None:
    """Refuse the request unless `token` is a fresh Turnstile pass (when enforced)."""
    secret = get_settings().TURNSTILE_SECRET_KEY
    if not secret:
        return

    if not token or len(token) > MAX_TOKEN_LENGTH:
        codes = ["missing-input-response"]
    else:
        try:
            codes = await _check(secret, token)
        except SiteverifyUnavailableError as exc:
            log_throttled(
                "captcha:unavailable",
                logging.ERROR,
                "captcha.unavailable reason=%s form=%s",
                exc,
                form,
            )
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE) from exc

    if not codes:
        return
    if {"invalid-input-secret", "missing-input-secret"} & set(codes):
        # Our configuration, not the visitor: say so loudly, but don't blame them.
        log_throttled(
            "captcha:secret", logging.ERROR, "captcha.misconfigured codes=%s", ",".join(codes)
        )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)

    log_throttled(
        f"captcha:failed:{form}:{ip}",
        logging.WARNING,
        "captcha.failed form=%s ip=%s codes=%s",
        form,
        ip,
        ",".join(codes),
    )
    raise HTTPException(status.HTTP_400_BAD_REQUEST, FAILED)
