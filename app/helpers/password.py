"""
Rules for new passwords (sign-up and password changes). The source of truth: the
frontend applies the same rules (app/lib/password/rules.ts there) only for instant
feedback. Keep the two in step.

Passwords are normalized with NFKC before they're checked and before Supabase hashes
them, so the same password always gives the same hash. There are no composition rules
(uppercase, digits, symbols): any character is fine, spaces and emoji included.

Supabase stores passwords (bcrypt, salted per password); this service never stores or
logs them.
"""

import hashlib
import logging
import unicodedata
from functools import cache
from itertools import pairwise
from pathlib import Path

import httpx
from fastapi import HTTPException, status

log = logging.getLogger(__name__)

MIN_LENGTH = 8
# bcrypt only reads 72 bytes and Supabase rejects longer passwords. Over the limit is
# an error, never a silent cut.
MAX_BYTES = 72
APP_NAMES = ("skolarly", "alteris")
# Shorter email names ("jo@...") would block too many ordinary passwords.
MIN_CONTEXT_TERM_LENGTH = 3

TOO_SHORT = f"Use at least {MIN_LENGTH} characters."
TOO_LONG = f"Use at most {MAX_BYTES} characters. Emoji and some letters count as more than one."
SEQUENCE = "Avoid repeated or sequential characters like 'aaaa' or '1234'."
PERSONAL = "Your password can't contain your email address."
APP_NAME = "Your password can't contain the name of this app."
COMMON = "That's a common password or word on its own. Try a few words together instead."
BREACHED = "This password has appeared in a data breach. Please choose a different one."

# Keyboard runs: a password that is any stretch of these, either way round.
KEYBOARD_RUNS = (
    "1234567890",
    "qwertyuiop",
    "asdfghjkl",
    "zxcvbnm",
    "qwertyuiopasdfghjklzxcvbnm",
)

# Common passwords and English words of 8+ characters, lowercased. Generated from the
# frontend's zxcvbn dictionaries by its scripts/export-password-blocklist.mjs.
BLOCKLIST_FILE = Path(__file__).with_name("common_passwords.txt")

RANGE_URL = "https://api.pwnedpasswords.com/range/"


def normalize(password: str) -> str:
    return unicodedata.normalize("NFKC", password)


@cache
def _blocklist() -> frozenset[str]:
    return frozenset(BLOCKLIST_FILE.read_text(encoding="utf-8").split("\n")) - {""}


def is_repeated_or_sequential(password: str) -> bool:
    """
    "aaaaaaaa", "abababab", "abcdabcd": one chunk of up to 4 characters repeated.
    "abcdefgh", "12345678", "87654321": each character one after or before the last.
    "qwertyui", "lkjhgfds": a stretch of a keyboard row.
    """
    lower = password.lower()
    points = [ord(c) for c in lower]
    if len(points) < 2:
        return False
    for size in range(1, min(4, len(points) - 1) + 1):
        if all(p == points[i % size] for i, p in enumerate(points)):
            return True
    for step in (1, -1):
        if all(b - a == step for a, b in pairwise(points)):
            return True
    return any(lower in run or lower[::-1] in run for run in KEYBOARD_RUNS)


def email_name(email: str | None) -> str | None:
    local = normalize(email or "").split("@")[0].strip().lower()
    return local if len(local) >= MIN_CONTEXT_TERM_LENGTH else None


def problem(password: str, email: str | None) -> str | None:
    """
    The first rule `password` breaks, as a message to show, or None when it passes.
    Everything except the breach check, which needs the network. `password` must
    already be normalized.
    """
    if len(password) < MIN_LENGTH:  # code points
        return TOO_SHORT
    if len(password.encode("utf-8")) > MAX_BYTES:
        return TOO_LONG
    if is_repeated_or_sequential(password):
        return SEQUENCE
    lower = password.lower()
    name = email_name(email)
    if name and name in lower:
        return PERSONAL
    if any(app in lower for app in APP_NAMES):
        return APP_NAME
    if lower in _blocklist():
        return COMMON
    return None


async def _range(prefix: str) -> str:
    # Add-Padding hides which prefix was asked for from anyone watching response sizes.
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
        response = await client.get(RANGE_URL + prefix, headers={"Add-Padding": "true"})
    response.raise_for_status()
    return response.text


async def is_breached(password: str) -> bool:
    """
    Whether `password` appears in Have I Been Pwned's Pwned Passwords, by k-anonymity:
    only the first 5 hex characters of its SHA-1 are sent, and the suffixes that come
    back are compared here. Fails open: if the service can't be reached the password is
    allowed and the error logged, so an outage never blocks sign-up.
    """
    digest = hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    try:
        body = await _range(prefix)
    except httpx.HTTPError as exc:
        log.error("Pwned Passwords check failed, allowing the password: %r", exc)
        return False
    for line in body.splitlines():
        candidate, _, count = line.strip().partition(":")
        # Padding entries have a count of 0.
        if candidate == suffix and count.strip() not in {"", "0"}:
            return True
    return False


async def check(password: str, email: str | None) -> None:
    """Refuse (422) a new password that breaks any rule. `password` must be normalized."""
    message = problem(password, email)
    if message is None and await is_breached(password):
        message = BREACHED
    if message is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, message)
