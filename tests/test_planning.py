import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
from pydantic import ValidationError

from app.crud import planning
from app.crud.attendance import TermSpan
from app.crud.planning import EntryWeek
from app.db.schemas.planning import EntryIn, LessonKey, RecordWrite

# Term II 2026 starts on a Wednesday and ends on a Friday.
TERM = TermSpan("Term II", 2026, date(2026, 5, 27), date(2026, 8, 21))
NOW = datetime(2026, 6, 10, 9, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Term weeks
# ---------------------------------------------------------------------------
def test_week_one_is_the_week_the_term_starts_in() -> None:
    assert planning.term_week(date(2026, 5, 27), TERM) == 1
    # The Sunday after: still week 1. The Monday after: week 2.
    assert planning.term_week(date(2026, 5, 31), TERM) == 1
    assert planning.term_week(date(2026, 6, 1), TERM) == 2
    assert planning.term_week(date(2026, 8, 21), TERM) == 13


def test_a_terms_weeks_stay_inside_the_term() -> None:
    assert planning.term_week_count(TERM) == 13
    assert planning.week_span(TERM, 1) == (date(2026, 5, 27), date(2026, 5, 31))
    assert planning.week_span(TERM, 2) == (date(2026, 6, 1), date(2026, 6, 7))
    assert planning.week_span(TERM, 13) == (date(2026, 8, 17), date(2026, 8, 21))


# ---------------------------------------------------------------------------
# The phone's clock and late plans
# ---------------------------------------------------------------------------
def test_the_phones_time_is_trusted_within_the_grace_window() -> None:
    written_offline = NOW - timedelta(hours=30)
    assert planning.trusted_time(written_offline, NOW) == written_offline
    # A little clock drift is fine; the future and the distant past aren't believed.
    assert planning.trusted_time(NOW + timedelta(minutes=2), NOW) == NOW + timedelta(minutes=2)
    assert planning.trusted_time(NOW + timedelta(hours=1), NOW) == NOW
    assert planning.trusted_time(NOW - timedelta(hours=73), NOW) == NOW


def test_a_plan_first_written_after_its_lesson_started_is_late() -> None:
    starts = datetime(2026, 6, 10, 8, 0, tzinfo=UTC)
    assert not planning.is_late(starts - timedelta(hours=12), starts)
    assert not planning.is_late(starts, starts)
    assert planning.is_late(starts + timedelta(minutes=1), starts)


def test_lesson_length_in_minutes() -> None:
    assert planning.lesson_minutes(time(8, 0), time(9, 20)) == 80
    assert planning.lesson_minutes(time(8, 0), None) is None


# ---------------------------------------------------------------------------
# Sign-off
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("draft", "draft"),
        ("submitted", "submitted"),
        ("sent_back", "sent_back"),
        ("approved", "submitted"),
    ],
)
def test_changing_an_approved_scheme_submits_it_again(before: str, after: str) -> None:
    assert planning.status_after_edit(before) == after


def test_drafts_and_sent_back_schemes_can_be_submitted() -> None:
    assert planning.can_submit("draft")
    assert planning.can_submit("sent_back")
    assert not planning.can_submit("submitted")
    assert not planning.can_submit("approved")


def _entry(week: int, topic: str, entry_id: uuid.UUID | None = None, lessons: int = 2) -> EntryIn:
    return EntryIn(id=entry_id or uuid.uuid4(), week=week, topic=topic, lessons=lessons)


def test_what_changed_since_approval() -> None:
    cells, division, food = _entry(3, "Cells"), _entry(4, "Cell division"), _entry(5, "Food tests")
    approved = [planning.entry_snapshot(e) for e in (cells, division, food)]
    reworded = cells.model_copy(update={"objectives": "Name the parts of a cell"})
    added = _entry(6, "Enzymes")
    now = [planning.entry_snapshot(e) for e in (reworded, division, added)]

    changed, removed = planning.changes_since_approval(approved, now)

    assert changed == {str(reworded.id), str(added.id)}
    assert removed == ["Week 5: Food tests"]


def test_a_never_approved_scheme_has_no_changes() -> None:
    assert planning.changes_since_approval(None, [planning.entry_snapshot(_entry(1, "Cells"))]) == (
        set(),
        [],
    )


def test_reordering_within_a_week_isnt_a_change() -> None:
    entry = _entry(2, "Cells")
    approved = [planning.entry_snapshot(entry)]
    moved = entry.model_copy(update={"position": 5})
    assert planning.changes_since_approval(approved, [planning.entry_snapshot(moved)]) == (
        set(),
        [],
    )


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------
def _weeks(*spec: tuple[int, int]) -> list[EntryWeek]:
    return [EntryWeek(uuid.uuid4(), week, lessons) for week, lessons in spec]


def test_progress_counts_finished_weeks_only() -> None:
    entries = _weeks((1, 2), (2, 2), (3, 3), (4, 2))
    covered = {entries[0].id, entries[1].id}
    # During week 3, covering weeks 1 and 2 is on time.
    assert planning.stream_progress(entries, covered, 3) == (2, 0, 4)
    # During week 5, with weeks 3 and 4 not covered: two weeks behind.
    assert planning.stream_progress(entries, covered, 5) == (2, 2, 9)


def test_a_week_is_covered_only_when_all_its_entries_are() -> None:
    entries = _weeks((1, 2), (2, 1), (2, 1), (3, 2))
    covered = {entries[0].id, entries[1].id, entries[3].id}
    through, behind, _ = planning.stream_progress(entries, covered, 4)
    assert (through, behind) == (1, 2)


def test_weeks_with_no_entries_dont_hold_a_stream_back() -> None:
    entries = _weeks((1, 2), (3, 2))
    through, behind, so_far = planning.stream_progress(entries, {entries[0].id, entries[1].id}, 4)
    assert (through, behind, so_far) == (3, 0, 4)


def test_without_a_current_week_nothing_is_behind() -> None:
    entries = _weeks((1, 2), (2, 2))
    assert planning.stream_progress(entries, set(), None) == (0, 0, 0)


def test_behind_stops_at_the_schemes_last_week() -> None:
    # Week 12 of a scheme that ends at week 2, fully covered: not behind.
    entries = _weeks((1, 2), (2, 2))
    assert planning.stream_progress(entries, {e.id for e in entries}, 12)[1] == 0


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------
def test_a_lesson_is_a_timetabled_lesson_or_a_full_extra_lesson() -> None:
    LessonKey(date=date(2026, 6, 10), lesson_id=uuid.uuid4())
    LessonKey(
        date=date(2026, 6, 10),
        class_name="S.2",
        stream="East",
        subject_id=uuid.uuid4(),
        starts_at=time(14, 0),
        minutes=40,
    )
    with pytest.raises(ValidationError):
        LessonKey(
            date=date(2026, 6, 10), class_name="S.2", subject_id=uuid.uuid4(), starts_at=time(14, 0)
        )


def test_not_taught_needs_a_reason_and_nothing_else_has_one() -> None:
    key = LessonKey(date=date(2026, 6, 10), lesson_id=uuid.uuid4())
    RecordWrite(lesson=key, status="not_taught", reason="school_event", recorded_at=NOW)
    RecordWrite(lesson=key, status="partly_taught", recorded_at=NOW)
    with pytest.raises(ValidationError):
        RecordWrite(lesson=key, status="not_taught", recorded_at=NOW)
    with pytest.raises(ValidationError):
        RecordWrite(lesson=key, status="taught", reason="other", recorded_at=NOW)
