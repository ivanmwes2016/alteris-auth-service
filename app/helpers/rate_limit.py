"""
Fixed-window rate limits in Upstash Redis (REST API), shared by every Lambda instance.

If Redis isn't configured or can't be reached, requests are allowed and the failure is
logged: an outage of the limiter shouldn't lock everyone out of signing in.
"""

import hashlib
import logging

import httpx
from fastapi import HTTPException, Request, status

from app.core.config import get_settings

log = logging.getLogger(__name__)


def _key(scope: str, subject: str) -> str:
    # Hash the subject so emails and IPs aren't stored in Redis in the clear.
    digest = hashlib.sha256(subject.strip().lower().encode()).hexdigest()[:32]
    return f"rl:{scope}:{digest}"


async def hit(scope: str, subject: str, *, limit: int, window_seconds: int) -> bool:
    """Count one attempt; True if it's within `limit` for the current window."""
    settings = get_settings()
    if not settings.REDIS_URL or not settings.REDIS_TOKEN:
        log.warning("Rate limiting skipped: Upstash Redis is not configured")
        return True

    key = _key(scope, subject)
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.post(
                f"{settings.REDIS_URL.rstrip('/')}/pipeline",
                headers={"Authorization": f"Bearer {settings.REDIS_TOKEN}"},
                json=[["INCR", key], ["EXPIRE", key, str(window_seconds), "NX"]],
            )
            response.raise_for_status()
            count = int(response.json()[0]["result"])
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        log.warning("Rate limiting skipped: Upstash request failed (%s)", type(exc).__name__)
        return True

    return count <= limit


async def enforce(scope: str, subject: str, *, limit: int, window_seconds: int) -> None:
    if not await hit(scope, subject, limit=limit, window_seconds=window_seconds):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please wait a while and try again.",
        )


def client_ip(request: Request) -> str:
    # Behind API Gateway the caller is the first X-Forwarded-For entry.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
