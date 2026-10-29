from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.crud import attendance
from app.crud.attendance import SaveCheck, TermSpan
from app.helpers import timezones

KAMPALA = ZoneInfo("Africa/Kampala")  # UTC+3
TUESDAY = date(2026, 10, 20)


def at(day: date, hour: int, minute: int = 0, tz: ZoneInfo = KAMPALA) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)


# ---------------------------------------------------------------------------
# Who can do what
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("is_owner", "role_name", "takes", "approves"),
    [
        (True, "bursar", True, True),
        (False, "head_teacher", True, True),
        (False, "admin", True, True),
        (False, "teacher", True, False),
        (False, "bursar", False, False),
        (False, "admissions_officer", False, False),
    ],
)
def test_roles(is_owner: bool, role_name: str, takes: bool, approves: bool) -> None:
    assert attendance.can_take_registers(is_owner=is_owner, role_name=role_name) is takes
    assert attendance.is_approver(is_owner=is_owner, role_name=role_name) is approves


@pytest.mark.parametrize(
    ("status", "listed"),
    [
        ("enrolled", True),
        ("Accepted", True),
        (" enrolled ", True),
        ("graduated", False),
        ("suspended", False),
        (None, False),
        ("", False),
    ],
)
def test_only_enrolled_and_accepted_students_are_listed(status: str | None, listed: bool) -> None:
    assert attendance.is_listed(status) is listed


def test_an_empty_subject_list_means_every_subject() -> None:
    assert attendance.offers_subject([], "biology")
    assert attendance.offers_subject(["biology"], "biology")
    assert not attendance.offers_subject(["chemistry"], "biology")


# ---------------------------------------------------------------------------
# The day lock and the grace window
# ---------------------------------------------------------------------------
def test_the_school_day_is_the_schools_date_not_utc() -> None:
    # 22:30 UTC Tuesday is already Wednesday in Kampala.
    now = datetime(2026, 10, 20, 22, 30, tzinfo=UTC)
    assert attendance.school_today(KAMPALA, now) == TUESDAY + timedelta(days=1)


def test_same_day_marks_save() -> None:
    check = attendance.check_save(
        register_date=TUESDAY, tapped_at=at(TUESDAY, 8), now=at(TUESDAY, 15), tz=KAMPALA
    )
    assert check is SaveCheck.OK


def test_an_offline_register_syncing_within_72_hours_saves() -> None:
    # Friday's register, taken offline, reaching the server on Monday morning.
    friday = date(2026, 10, 23)
    check = attendance.check_save(
        register_date=friday,
        tapped_at=at(friday, 8),
        now=at(friday + timedelta(days=3), 7),
        tz=KAMPALA,
    )
    assert check is SaveCheck.OK


def test_after_the_grace_window_the_register_is_locked() -> None:
    after = attendance.day_end(TUESDAY, KAMPALA) + attendance.GRACE + timedelta(minutes=1)
    check = attendance.check_save(
        register_date=TUESDAY, tapped_at=at(TUESDAY, 8), now=after, tz=KAMPALA
    )
    assert check is SaveCheck.LOCKED


def test_a_tap_on_another_day_is_a_correction_not_an_edit() -> None:
    # Filling Tuesday in on Wednesday isn't an offline sync.
    wednesday = TUESDAY + timedelta(days=1)
    check = attendance.check_save(
        register_date=TUESDAY, tapped_at=at(wednesday, 9), now=at(wednesday, 9), tz=KAMPALA
    )
    assert check is SaveCheck.BAD_TAP_TIME


def test_taps_from_the_future_are_refused_but_small_clock_drift_is_not() -> None:
    now = at(TUESDAY, 9)
    drift = attendance.check_save(
        register_date=TUESDAY, tapped_at=now + timedelta(minutes=2), now=now, tz=KAMPALA
    )
    future = attendance.check_save(
        register_date=TUESDAY, tapped_at=now + timedelta(hours=1), now=now, tz=KAMPALA
    )
    assert drift is SaveCheck.OK
    assert future is SaveCheck.BAD_TAP_TIME


def test_registers_for_future_days_cant_be_taken() -> None:
    check = attendance.check_save(
        register_date=TUESDAY + timedelta(days=1),
        tapped_at=at(TUESDAY, 9),
        now=at(TUESDAY, 9),
        tz=KAMPALA,
    )
    assert check is SaveCheck.FUTURE_REGISTER


def test_the_latest_tap_wins() -> None:
    earlier, later = at(TUESDAY, 8), at(TUESDAY, 10)
    assert attendance.is_newer_tap(None, earlier)
    assert attendance.is_newer_tap(earlier, later)
    assert not attendance.is_newer_tap(later, earlier)


# ---------------------------------------------------------------------------
# Terms: the correction window and register gaps
# ---------------------------------------------------------------------------
TERMS = [
    TermSpan("Term I", 2026, date(2026, 2, 2), date(2026, 4, 30)),
    TermSpan("Term II", 2026, date(2026, 5, 25), date(2026, 8, 21)),
    TermSpan("Term III", 2026, date(2026, 9, 14), date(2026, 12, 4)),
]


def test_corrections_reach_back_to_the_start_of_the_previous_term() -> None:
    assert attendance.correction_window_start(date(2026, 10, 29), TERMS) == date(2026, 5, 25)


def test_in_the_holidays_the_current_term_is_the_last_to_start() -> None:
    assert attendance.correction_window_start(date(2026, 9, 1), TERMS) == date(2026, 2, 2)


def test_without_term_dates_corrections_reach_back_a_fixed_number_of_days() -> None:
    today = date(2026, 10, 29)
    assert attendance.correction_window_start(today, []) == today - timedelta(days=120)


def test_gaps_count_only_on_finished_term_days_that_werent_closed() -> None:
    today = date(2026, 10, 29)
    closed = {date(2026, 10, 9)}
    assert attendance.counts_for_gaps(date(2026, 10, 20), today=today, terms=TERMS, closed=closed)
    assert not attendance.counts_for_gaps(today, today=today, terms=TERMS, closed=closed)
    assert not attendance.counts_for_gaps(
        date(2026, 10, 9), today=today, terms=TERMS, closed=closed
    )
    assert not attendance.counts_for_gaps(date(2026, 9, 1), today=today, terms=TERMS, closed=closed)


# ---------------------------------------------------------------------------
# Attendance rate
# ---------------------------------------------------------------------------
def test_late_counts_as_attended_and_excused_is_left_out() -> None:
    assert attendance.attendance_rate(present=6, late=2, absent=2) == 0.8


def test_no_counted_marks_means_no_rate() -> None:
    assert attendance.attendance_rate(present=0, late=0, absent=0) is None


# ---------------------------------------------------------------------------
# Timezones
# ---------------------------------------------------------------------------
def test_a_schools_own_timezone_wins() -> None:
    tz = timezones.school_timezone(explicit="Europe/London", country="Uganda")
    assert tz.key == "Europe/London"


def test_single_zone_countries_get_their_zone() -> None:
    assert timezones.school_timezone(explicit=None, country=" Kenya ").key == "Africa/Nairobi"
    assert timezones.school_timezone(explicit=None, country="United Kingdom").key == "Europe/London"


def test_multi_zone_or_unknown_countries_fall_back_to_east_africa_time() -> None:
    assert timezones.country_timezone("United States") is None
    assert timezones.school_timezone(explicit=None, country="United States").key == "Africa/Kampala"
    assert timezones.school_timezone(explicit="Not/AZone", country=None).key == "Africa/Kampala"
