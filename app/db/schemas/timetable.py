"""
Pydantic schemas for the Timetable page. Fields are camelCase on the wire
(the frontend's types), snake_case in Python. Days are 0 = Monday … 6 = Sunday.
"""

import uuid
from datetime import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

Day = int


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


def _check_days(days: list[int]) -> list[int]:
    if any(d < 0 or d > 6 for d in days):
        raise ValueError("Days run from 0 (Monday) to 6 (Sunday)")
    return sorted(set(days))


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
class PeriodRead(_Camel):
    id: uuid.UUID
    starts_at: time
    ends_at: time
    kind: Literal["lesson", "break"]
    label: str | None = None


class LessonRead(_Camel):
    id: uuid.UUID
    class_id: uuid.UUID
    subject_id: uuid.UUID
    staff_id: uuid.UUID | None = None
    day: Day
    period_id: uuid.UUID
    length: int


class OptionsPeriodRead(_Camel):
    id: uuid.UUID
    class_id: uuid.UUID
    day: Day
    period_id: uuid.UUID


class TimetableSummary(_Camel):
    id: uuid.UUID
    term: str
    year: int


class TimetableRead(TimetableSummary):
    days: list[Day]
    periods: list[PeriodRead]
    lessons: list[LessonRead]
    options_periods: list[OptionsPeriodRead]
    # The whole timetable (bell schedule, options periods, every class's lessons).
    # False for past terms and for people without the Manage Timetable roles.
    editable: bool
    # The classes whose lessons the caller may add, change, swap and remove: every
    # class for managers, their own class for class teachers, none for past terms.
    editable_class_ids: list[uuid.UUID] = []
    # The caller's staff record, to pick out their own lessons. None if there isn't one.
    staff_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
class TimetableCreate(_Camel):
    term: str = Field(..., min_length=1, max_length=100)
    year: int = Field(..., ge=2000, le=2100)
    # Another of the school's timetables to copy the bell schedule, days and lessons from.
    copy_from: uuid.UUID | None = None


class PeriodWrite(_Camel):
    # Present for an existing period (its lessons stay put); absent for a new one.
    id: uuid.UUID | None = None
    starts_at: time
    ends_at: time
    kind: Literal["lesson", "break"] = "lesson"
    label: str | None = Field(None, max_length=60)

    @model_validator(mode="after")
    def _times_and_label(self) -> "PeriodWrite":
        if self.starts_at >= self.ends_at:
            raise ValueError("A period must end after it starts")
        if self.kind == "break" and not (self.label or "").strip():
            raise ValueError("A break needs a name, e.g. Lunch")
        if self.kind == "lesson":
            self.label = None
        return self


class BellScheduleWrite(_Camel):
    """The whole bell schedule, in order, plus the school days."""

    days: list[Day] = Field(..., max_length=7)
    periods: list[PeriodWrite] = Field(..., max_length=30)

    _days = field_validator("days")(_check_days)

    @model_validator(mode="after")
    def _in_order(self) -> "BellScheduleWrite":
        for before, after in zip(self.periods, self.periods[1:], strict=False):
            if after.starts_at < before.ends_at:
                raise ValueError("Periods must be in time order and must not overlap")
        return self


class BellScheduleImpact(_Camel):
    """What saving a bell schedule would do to existing lessons."""

    removed_lessons: int
    shortened_lessons: int


class LessonCreate(_Camel):
    class_id: uuid.UUID
    subject_id: uuid.UUID
    staff_id: uuid.UUID | None = None
    day: Day = Field(..., ge=0, le=6)
    period_id: uuid.UUID
    length: int = Field(1, ge=1, le=10)


class LessonUpdate(_Camel):
    """Edit or move a lesson. `staffId: null` unassigns the teacher."""

    subject_id: uuid.UUID | None = None
    staff_id: uuid.UUID | None = None
    day: Day | None = Field(None, ge=0, le=6)
    period_id: uuid.UUID | None = None
    length: int | None = Field(None, ge=1, le=10)


class LessonSwap(_Camel):
    """Two lessons trade places (day and first period); each keeps its length."""

    first_id: uuid.UUID
    second_id: uuid.UUID


class OptionsPeriodCreate(_Camel):
    class_id: uuid.UUID
    day: Day = Field(..., ge=0, le=6)
    period_id: uuid.UUID
