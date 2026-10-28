import uuid
from datetime import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.crud import timetable
from app.crud.team import Actor
from app.db.schemas.timetable import BellScheduleWrite, LessonUpdate, PeriodWrite


def _period(kind: str = "lesson") -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), kind=kind)


# P1 P2 P3 | break | P4 P5
P1, P2, P3, BREAK, P4, P5 = (
    _period(),
    _period(),
    _period(),
    _period("break"),
    _period(),
    _period(),
)
DAY = [P1, P2, P3, BREAK, P4, P5]


def _placement(period: SimpleNamespace, length: int = 1, day: int = 0) -> timetable.Placement:
    return timetable.Placement(id=uuid.uuid4(), day=day, period_id=period.id, length=length)


# ---------------------------------------------------------------------------
# Permissions and past terms
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("is_owner", "role_name", "allowed"),
    [
        (True, "teacher", True),
        (False, "head_teacher", True),
        (False, "admin", True),
        (False, "teacher", False),
        (False, "bursar", False),
        (False, "admissions_officer", False),
    ],
)
def test_only_the_manage_timetable_roles_can_edit(
    is_owner: bool, role_name: str, allowed: bool
) -> None:
    assert timetable.can_manage_timetable(is_owner=is_owner, role_name=role_name) is allowed


LABELS = ["Term I", "Term II", "Term III"]


@pytest.mark.parametrize(
    ("term", "year", "past"),
    [
        ("Term III", 2025, True),  # an earlier year
        ("Term I", 2026, True),  # earlier this year
        ("Term II", 2026, False),  # the current term
        ("Term III", 2026, False),  # later this year
        ("Term I", 2027, False),  # next year
        ("Old name", 2026, False),  # a label the school no longer uses
    ],
)
def test_terms_before_the_current_one_are_past(term: str, year: int, past: bool) -> None:
    assert (
        timetable.is_past_term(
            labels=LABELS, current_label="Term II", current_year=2026, term=term, year=year
        )
        is past
    )


def test_nothing_is_past_until_the_school_sets_a_current_term() -> None:
    assert not timetable.is_past_term(
        labels=LABELS, current_label=None, current_year=None, term="Term I", year=2020
    )


# ---------------------------------------------------------------------------
# Where lessons can go
# ---------------------------------------------------------------------------
def test_a_teaching_run_stops_at_a_break() -> None:
    assert timetable.teaching_run(DAY, P1.id) == 3
    assert timetable.teaching_run(DAY, P3.id) == 1
    assert timetable.teaching_run(DAY, P4.id) == 2


def test_breaks_and_unknown_periods_have_no_teaching_run() -> None:
    assert timetable.teaching_run(DAY, BREAK.id) == 0
    assert timetable.teaching_run(DAY, uuid.uuid4()) == 0


# ---------------------------------------------------------------------------
# Changing the bell schedule
# ---------------------------------------------------------------------------
def test_unchanged_schedule_touches_no_lessons() -> None:
    lessons = [_placement(P1, 3), _placement(P4, 2)]
    assert timetable.plan_bell_schedule(DAY, [0, 1, 2, 3, 4], lessons) == (set(), {})


def test_lessons_in_a_removed_period_are_removed() -> None:
    lesson = _placement(P2)
    removed, shortened = timetable.plan_bell_schedule([P1, P3], [0], [lesson])
    assert removed == {lesson.id}
    assert shortened == {}


def test_lessons_in_a_period_turned_into_a_break_are_removed() -> None:
    lesson = _placement(P1)
    now_a_break = SimpleNamespace(id=P1.id, kind="break")
    removed, _ = timetable.plan_bell_schedule([now_a_break, P2], [0], [lesson])
    assert removed == {lesson.id}


def test_lessons_on_a_day_switched_off_are_removed() -> None:
    saturday = _placement(P1, day=5)
    monday = _placement(P1, day=0)
    removed, _ = timetable.plan_bell_schedule(DAY, [0, 1, 2, 3, 4], [saturday, monday])
    assert removed == {saturday.id}


def test_a_double_lesson_losing_its_second_period_is_shortened() -> None:
    double = _placement(P2, 2)
    removed, shortened = timetable.plan_bell_schedule([P1, P2, BREAK, P4], [0], [double])
    assert removed == set()
    assert shortened == {double.id: 1}


def test_a_new_break_inside_a_double_lesson_shortens_it() -> None:
    double = _placement(P1, 2)
    new_break = SimpleNamespace(id=None, kind="break")
    _, shortened = timetable.plan_bell_schedule([P1, new_break, P2, P3], [0], [double])
    assert shortened == {double.id: 1}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
def _write(start: str, end: str, kind: str = "lesson", label: str | None = None) -> dict:
    return {"startsAt": start, "endsAt": end, "kind": kind, "label": label}


def test_bell_schedule_reads_camel_case_and_sorts_days() -> None:
    schedule = BellScheduleWrite.model_validate(
        {"days": [4, 0, 0, 6], "periods": [_write("08:00", "08:40")]}
    )
    assert schedule.days == [0, 4, 6]
    assert schedule.periods[0].starts_at == time(8, 0)


def test_days_outside_the_week_are_refused() -> None:
    with pytest.raises(ValidationError):
        BellScheduleWrite.model_validate({"days": [7], "periods": []})


def test_overlapping_periods_are_refused() -> None:
    with pytest.raises(ValidationError, match="overlap"):
        BellScheduleWrite.model_validate(
            {"days": [0], "periods": [_write("08:00", "08:40"), _write("08:30", "09:10")]}
        )


def test_a_period_must_end_after_it_starts() -> None:
    with pytest.raises(ValidationError, match="end after"):
        PeriodWrite.model_validate(_write("09:00", "08:00"))


def test_a_break_needs_a_name_and_a_teaching_period_drops_one() -> None:
    with pytest.raises(ValidationError, match="name"):
        PeriodWrite.model_validate(_write("10:00", "10:20", "break"))
    assert PeriodWrite.model_validate(_write("08:00", "08:40", "lesson", "P1")).label is None


def test_a_lesson_update_can_unassign_the_teacher() -> None:
    update = LessonUpdate.model_validate({"staffId": None})
    assert update.model_dump(exclude_unset=True) == {"staff_id": None}


def test_the_full_read_is_camel_case() -> None:
    period = SimpleNamespace(
        id=uuid.uuid4(), starts_at=time(8), ends_at=time(8, 40), kind="lesson", label=None
    )
    table = SimpleNamespace(
        id=uuid.uuid4(),
        term="Term II",
        year=2026,
        days=[0, 1],
        periods=[period],
        lessons=[],
        options_periods=[],
    )
    viewer = timetable.Viewer(
        actor=Actor(
            user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), role_name="teacher", is_owner=False
        ),
        can_manage=False,
        labels=LABELS,
        current_label="Term II",
        current_year=2026,
    )
    body = timetable._read(viewer, table).model_dump(by_alias=True)  # type: ignore[arg-type]
    assert body["periods"][0]["startsAt"] == time(8)
    assert body["optionsPeriods"] == []
    assert body["editable"] is False


# ---------------------------------------------------------------------------
# Editing guards
# ---------------------------------------------------------------------------
def _viewer(*, can_manage: bool) -> timetable.Viewer:
    return timetable.Viewer(
        actor=Actor(
            user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), role_name="admin", is_owner=False
        ),
        can_manage=can_manage,
        labels=LABELS,
        current_label="Term II",
        current_year=2026,
    )


def test_editing_without_the_role_is_forbidden() -> None:
    with pytest.raises(HTTPException) as err:
        timetable._require_editable(
            _viewer(can_manage=False),
            SimpleNamespace(term="Term II", year=2026),  # type: ignore[arg-type]
        )
    assert err.value.status_code == 403


def test_past_terms_cant_be_edited_even_by_managers() -> None:
    with pytest.raises(HTTPException) as err:
        timetable._require_editable(
            _viewer(can_manage=True),
            SimpleNamespace(term="Term I", year=2026),  # type: ignore[arg-type]
        )
    assert err.value.status_code == 409


def test_a_lesson_must_fit_before_the_next_break() -> None:
    table = SimpleNamespace(days=[0, 1, 2, 3, 4], periods=DAY)
    timetable._check_place(table, day=0, period_id=P2.id, length=2)  # type: ignore[arg-type]
    for day, period, length in [(5, P1, 1), (0, BREAK, 1), (0, P3, 2)]:
        with pytest.raises(HTTPException) as err:
            timetable._check_place(table, day=day, period_id=period.id, length=length)  # type: ignore[arg-type]
        assert err.value.status_code == 422
