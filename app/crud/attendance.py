"""
Service layer for attendance registers.

A register is a class's daily roll call or one lesson of one subject, on one
school day (a date in the school's own timezone). Students start Unmarked: no
mark row. Teachers, Head Teachers, Admins and the Owner can take any register
in their school; Bursars and Admissions Officers can't.

Marks are free to change until the school day ends. After that any change,
including marking someone who was left Unmarked, is a correction: approvers
(Owner, Head Teacher, Admin) make it directly with a reason, everyone else
sends a correction request that an approver accepts or rejects. A register
taken offline on the day still saves normally if it arrives within 72 hours,
judged by when it was tapped on the phone; the latest tap wins (see
docs/adr/0001 in the marking app repo).

Every query is scoped to the caller's tenant, and every student, subject,
class and lesson id is checked against it: ids from other schools 404.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

TAKE_REGISTER_ROLES = {"teacher", "head_teacher", "admin"}
APPROVER_ROLES = {"head_teacher", "admin"}
# Student.status is free text; only these are on registers.
LISTED_STUDENT_STATUSES = {"enrolled", "accepted"}

GRACE = timedelta(hours=72)
# Phones' clocks drift a little; a tap this far "in the future" is still accepted.
CLOCK_SKEW = timedelta(minutes=5)
# How far back a correction request can reach when the school has no term dates.
FALLBACK_CORRECTION_DAYS = 120


# ---------------------------------------------------------------------------
# Pure rules (unit-tested without a database)
# ---------------------------------------------------------------------------
def can_take_registers(*, is_owner: bool, role_name: str) -> bool:
    return is_owner or role_name in TAKE_REGISTER_ROLES


def is_approver(*, is_owner: bool, role_name: str) -> bool:
    """Approvers correct past registers directly and decide correction requests."""
    return is_owner or role_name in APPROVER_ROLES


def is_listed(student_status: str | None) -> bool:
    return (student_status or "").strip().lower() in LISTED_STUDENT_STATUSES


def offers_subject(offered: Sequence[object], subject_id: object) -> bool:
    """A student with no subjects listed takes every subject their class takes."""
    return not offered or subject_id in offered


def school_today(tz: ZoneInfo, now: datetime) -> date:
    return now.astimezone(tz).date()


def day_end(day: date, tz: ZoneInfo) -> datetime:
    """The moment `day` ends at the school, i.e. its next midnight there."""
    return datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)


class SaveCheck(Enum):
    OK = "ok"
    FUTURE_REGISTER = "future_register"
    BAD_TAP_TIME = "bad_tap_time"
    LOCKED = "locked"


def check_save(
    *, register_date: date, tapped_at: datetime, now: datetime, tz: ZoneInfo
) -> SaveCheck:
    """
    Whether a mark tapped at `tapped_at` can be saved as a normal edit, not a
    correction. It must be tapped on the register's own school day, and arrive
    before that day ends or within the grace window after.
    """
    today = school_today(tz, now)
    if register_date > today:
        return SaveCheck.FUTURE_REGISTER
    if tapped_at > now + CLOCK_SKEW or tapped_at.astimezone(tz).date() != register_date:
        return SaveCheck.BAD_TAP_TIME
    if register_date == today or now <= day_end(register_date, tz) + GRACE:
        return SaveCheck.OK
    return SaveCheck.LOCKED


def is_newer_tap(existing: datetime | None, incoming: datetime) -> bool:
    """Latest tap wins: an older copy synced late never overwrites a newer mark."""
    return existing is None or incoming >= existing


@dataclass(frozen=True)
class TermSpan:
    label: str
    year: int
    starts_on: date
    ends_on: date


def term_on(day: date, terms: Sequence[TermSpan]) -> TermSpan | None:
    return next((t for t in terms if t.starts_on <= day <= t.ends_on), None)


def correction_window_start(today: date, terms: Sequence[TermSpan]) -> date:
    """
    The earliest date a correction request can reach: the start of the previous
    term (the current one being the latest to have started). With no term dates,
    a fixed number of days back.
    """
    started = sorted((t for t in terms if t.starts_on <= today), key=lambda t: t.starts_on)
    if not started:
        return today - timedelta(days=FALLBACK_CORRECTION_DAYS)
    return started[-2].starts_on if len(started) >= 2 else started[-1].starts_on


def counts_for_gaps(
    day: date, *, today: date, terms: Sequence[TermSpan], closed: set[date]
) -> bool:
    """A finished school day inside a term that wasn't a no-school day."""
    return day < today and day not in closed and term_on(day, terms) is not None


def attendance_rate(*, present: int, late: int, absent: int) -> float | None:
    """(Present + Late) / (Present + Late + Absent); Excused and Unmarked are left out."""
    counted = present + late + absent
    return None if counted == 0 else (present + late) / counted


def now_utc() -> datetime:
    return datetime.now(UTC)
