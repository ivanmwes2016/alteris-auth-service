"""
Rate limits shared by every instance of the API, counted in Upstash Redis (REST API).

Each rule is a sliding window: a sorted set of request timestamps that one Lua script
trims, counts and adds to atomically, so concurrent requests on different instances
can't both slip under a limit. Rules have names; the defaults below can be overridden
per rule with an env var, e.g. RATE_LIMIT_LOGIN_IP=20/60 (count/seconds).

If Redis isn't configured or can't be reached, sensitive rules (sign-in, sign-up,
account emails, invites) fall back to the same window counted in this process, so an
outage weakens protection but never removes it; general API rules let requests
through so ordinary school work carries on. Either way the outage is logged.

Emails, user ids and IPs are hashed before they become Redis keys. Log lines are
`rate_limit.<event> key=value ...` so they can be searched in Render's log explorer.
"""

import hashlib
import ipaddress
import logging
import math
import secrets
import time
from collections import deque
from dataclasses import dataclass

import httpx
from fastapi import HTTPException, Request, status

from app.core.config import get_settings

log = logging.getLogger(__name__)

# count/seconds. Account rules take a normalised email; user rules a user id.
DEFAULT_RULES: dict[str, str] = {
    "login_ip": "20/60",  # every sign-in attempt from one IP
    "login_account": "5/900",  # attempts on one account since its last successful sign-in
    "register_ip": "3/3600",  # new accounts from one IP
    "register_invited_ip": "30/3600",  # invitees signing up (a school adding its staff)
    "verify_account": "3/3600",  # confirmation emails to one address (signup + resend)
    "reset_account": "3/3600",  # password reset emails to one address
    "password_change": "10/3600",  # new passwords set by one user
    "email_ip": "20/3600",  # resend + reset requests from one IP
    "invite_user": "10/3600",  # invites sent or resent by one administrator
    "invite_tenant": "30/3600",  # ... and by one school, however many admins it has
    "api_user": "120/60",  # any authenticated request by one user
    "enquiry_ip": "60/3600",  # unauthenticated enquiry submissions from one IP
    "school_review_ip": "30/3600",  # approve/reject link actions from one IP
}

LOCAL_MAX_KEYS = 10_000
LOG_EVERY_SECONDS = 60
KEY_PREFIX = "rl2"  # rl: held the older fixed-window counters (plain strings)

# KEYS[1] sorted set; ARGV now_ms, window_ms, limit, unique member.
# Returns 0 when the request is counted, else milliseconds until a slot frees up.
SLIDING_WINDOW = """
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
if redis.call('ZCARD', KEYS[1]) < tonumber(ARGV[3]) then
  redis.call('ZADD', KEYS[1], now, ARGV[4])
  redis.call('PEXPIRE', KEYS[1], window)
  return 0
end
local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
return math.max(1, tonumber(oldest[2]) + window - now)
"""


@dataclass(frozen=True)
class Rule:
    name: str
    limit: int
    window_seconds: int


class RedisUnavailableError(Exception):
    pass


def _wall_ms() -> int:
    """Shared across instances (Redis timestamps); small clock skew only blurs edges."""
    return int(time.time() * 1000)


def _monotonic_ms() -> float:
    return time.monotonic() * 1000


def _parse(value: str) -> tuple[int, int]:
    count, seconds = value.split("/")
    limit, window = int(count), int(seconds)
    if limit < 1 or window < 1:
        raise ValueError(value)
    return limit, window


def rule(name: str) -> Rule:
    override = get_settings().RATE_LIMITS.get(name)
    if override:
        try:
            return Rule(name, *_parse(override))
        except ValueError:
            log_throttled(
                f"config:{name}",
                logging.ERROR,
                "rate_limit.bad_config rule=%s value=%r (expected count/seconds); using default",
                name,
                override,
            )
    return Rule(name, *_parse(DEFAULT_RULES[name]))


def _key(rule_name: str, subject: str) -> str:
    # Hash the subject so emails, user ids and IPs aren't stored in Redis in the clear.
    digest = hashlib.sha256(subject.strip().lower().encode()).hexdigest()[:32]
    return f"{KEY_PREFIX}:{rule_name}:{digest}"


# ── Throttled logging ────────────────────────────────────────────────────────
# One line per key per minute per instance: an attack mustn't flood the logs.
_last_logged: dict[str, float] = {}


def log_throttled(key: str, level: int, message: str, *args: object) -> None:
    now = time.monotonic()
    if now - _last_logged.get(key, -math.inf) < LOG_EVERY_SECONDS:
        return
    if len(_last_logged) >= LOCAL_MAX_KEYS:
        _last_logged.clear()
    _last_logged[key] = now
    log.log(level, message, *args)


# ── Redis (Upstash REST) ─────────────────────────────────────────────────────
_http: httpx.AsyncClient | None = None


def _http_client() -> httpx.AsyncClient:
    # Shared so each check reuses a warm TLS connection instead of opening one.
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=1.0))
    return _http


async def _redis(command: list[str]) -> object:
    settings = get_settings()
    if not settings.REDIS_URL or not settings.REDIS_TOKEN:
        raise RedisUnavailableError("not configured")
    try:
        response = await _http_client().post(
            settings.REDIS_URL.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.REDIS_TOKEN}"},
            json=command,
        )
        response.raise_for_status()
        return response.json()["result"]
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise RedisUnavailableError(type(exc).__name__) from exc


async def _redis_hit(key: str, limit: int, window_ms: int) -> int:
    now_ms = _wall_ms()
    member = f"{now_ms}-{secrets.token_hex(4)}"
    result = await _redis(
        ["EVAL", SLIDING_WINDOW, "1", key, str(now_ms), str(window_ms), str(limit), member]
    )
    if not isinstance(result, int):
        raise RedisUnavailableError(f"unexpected reply {type(result).__name__}")
    return result


# ── In-process fallback ──────────────────────────────────────────────────────
_local: dict[str, deque[float]] = {}


def _local_hit(key: str, limit: int, window_ms: int) -> int:
    now = _monotonic_ms()
    hits = _local.get(key)
    if hits is None:
        while len(_local) >= LOCAL_MAX_KEYS:
            _local.pop(next(iter(_local)))  # oldest key first
        hits = _local[key] = deque()
    while hits and hits[0] <= now - window_ms:
        hits.popleft()
    if len(hits) < limit:
        hits.append(now)
        return 0
    return max(1, math.ceil(hits[0] + window_ms - now))


def reset_local_state() -> None:
    _local.clear()
    _last_logged.clear()


# ── Public API ───────────────────────────────────────────────────────────────
async def hit(name: str, subject: str, *, sensitive: bool = True) -> int:
    """
    Count one request against rule `name` for `subject`. Returns 0 if it's allowed,
    else the seconds until it would be. `sensitive` picks the Redis-outage behaviour.
    """
    if not get_settings().RATE_LIMITING_ENABLED:
        return 0

    current = rule(name)
    key = _key(name, subject)
    window_ms = current.window_seconds * 1000
    try:
        retry_ms = await _redis_hit(key, current.limit, window_ms)
    except RedisUnavailableError as exc:
        log_throttled(
            "redis",
            logging.ERROR,
            "rate_limit.redis_unavailable reason=%s fallback=%s",
            exc,
            "local" if sensitive else "allow",
        )
        if not sensitive:
            return 0
        retry_ms = _local_hit(key, current.limit, window_ms)

    return math.ceil(retry_ms / 1000)


async def enforce(name: str, subject: str, *, sensitive: bool = True) -> None:
    """Count one request; 429 with Retry-After once `name`'s limit is used up."""
    retry_after = await hit(name, subject, sensitive=sensitive)
    if not retry_after:
        return

    # IPs are logged as-is (needed to investigate); anything else only by its hash.
    shown = subject if name.endswith("_ip") else _key(name, subject).rsplit(":", 1)[1][:12]
    log_throttled(
        _key(name, subject),
        logging.WARNING,
        "rate_limit.blocked rule=%s subject=%s retry_after=%s",
        name,
        shown,
        retry_after,
    )
    raise too_many_requests(retry_after)


async def reset(name: str, subject: str) -> None:
    """Forget `subject`'s requests under rule `name`, e.g. after a successful sign-in."""
    if not get_settings().RATE_LIMITING_ENABLED:
        return
    key = _key(name, subject)
    _local.pop(key, None)
    try:
        await _redis(["DEL", key])
    except RedisUnavailableError as exc:
        log_throttled("redis", logging.ERROR, "rate_limit.redis_unavailable reason=%s", exc)


def too_many_requests(retry_after: int) -> HTTPException:
    minutes = math.ceil(retry_after / 60)
    wait = (
        f"{retry_after} seconds"
        if retry_after < 60
        else f"{minutes} minute{'s' if minutes != 1 else ''}"
    )
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Too many attempts. Please try again in {wait}.",
        headers={"Retry-After": str(retry_after)},
    )


# ── Client IP ────────────────────────────────────────────────────────────────
_ip_source_logged = False


def _normalise_ip(value: str) -> str | None:
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if ip.version == 6:
        # One IPv6 connection usually owns a whole /64; limit the block, not each address.
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def client_ip(request: Request) -> str:
    """
    The caller's IP, from headers only our proxies can set. In production Cloudflare
    (in front of Render) overwrites CLIENT_IP_HEADER on every request. Otherwise we take
    the X-Forwarded-For entry TRUSTED_PROXY_HOPS from the right: entries further left
    were supplied by the client and can say anything.
    """
    settings = get_settings()
    forwarded = [part for part in request.headers.get("x-forwarded-for", "").split(",") if part]

    source, ip = "peer", None
    if settings.CLIENT_IP_HEADER and request.headers.get(settings.CLIENT_IP_HEADER):
        source = settings.CLIENT_IP_HEADER
        ip = _normalise_ip(request.headers[settings.CLIENT_IP_HEADER])
    elif settings.TRUSTED_PROXY_HOPS > 0 and len(forwarded) >= settings.TRUSTED_PROXY_HOPS:
        source = "x-forwarded-for"
        ip = _normalise_ip(forwarded[-settings.TRUSTED_PROXY_HOPS])
    if ip is None and request.client:
        source, ip = "peer", _normalise_ip(request.client.host)

    global _ip_source_logged
    if not _ip_source_logged:
        # Once per process, so a deploy shows which header client IPs come from.
        _ip_source_logged = True
        log.warning(
            "rate_limit.ip_source source=%s forwarded_entries=%s (startup notice)",
            source,
            len(forwarded),
        )
    return ip or "unknown"
