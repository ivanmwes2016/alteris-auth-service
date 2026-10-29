"""
Pydantic schemas for attendance registers. Fields are camelCase on the wire,
snake_case in Python. Statuses are the stored values: the app shows
"Unexcused" as Absent.
"""

import uuid
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

# A field called `date` shadows the type inside a model, hence the alias.
CalendarDate = date

Status = Literal["Present", "Late", "Excused", "Unexcused"]
RegisterKind = Literal["daily", "lesson"]
RegisterState = Literal["open", "locked", "future"]


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


# ---------------------------------------------------------------------------
# Which register
# ---------------------------------------------------------------------------
class RegisterKey(_Camel):
    """
    A daily register: kind "daily", date, className (stream only narrows the list).
    A timetabled lesson: kind "lesson", date, lessonId.
    An ad-hoc lesson: kind "lesson", date, className, optional stream, subjectId, startsAt.
    """

    kind: RegisterKind
    date: CalendarDate
    class_name: str | None = Field(None, min_length=1, max_length=100)
    stream: str | None = Field(None, min_length=1, max_length=100)
    lesson_id: uuid.UUID | None = None
    subject_id: uuid.UUID | None = None
    starts_at: time | None = None

    @model_validator(mode="after")
    def _identifies_one_register(self) -> "RegisterKey":
        if self.kind == "daily":
            if not self.class_name:
                raise ValueError("A daily register needs a className")
            if self.lesson_id or self.subject_id or self.starts_at:
                raise ValueError("A daily register has no lesson, subject or start time")
        elif self.lesson_id is None and not (
            self.class_name and self.subject_id and self.starts_at
        ):
            raise ValueError(
                "A lesson register needs a lessonId, or className, subjectId and startsAt"
            )
        return self


# ---------------------------------------------------------------------------
# Reading a register
# ---------------------------------------------------------------------------
class MarkRead(_Camel):
    status: Status
    note: str | None = None
    marked_by_name: str | None = None
    tapped_at: datetime


class RegisterStudent(_Camel):
    student_id: uuid.UUID
    name: str
    admission_number: str | None = None
    stream: str | None = None
    photo: str | None = None
    mark: MarkRead | None = None
    pending_correction: bool = False


class RegisterCounts(_Camel):
    present: int = 0
    late: int = 0
    unexcused: int = 0
    excused: int = 0
    unmarked: int = 0


class RegisterRead(_Camel):
    register_id: uuid.UUID | None = None
    key: RegisterKey
    kind: RegisterKind
    date: CalendarDate
    class_name: str
    stream: str | None = None
    subject_id: uuid.UUID | None = None
    subject_name: str | None = None
    lesson_id: uuid.UUID | None = None
    starts_at: time | None = None
    ends_at: time | None = None
    teacher_name: str | None = None
    state: RegisterState
    can_edit: bool
    can_correct: bool
    can_request_correction: bool
    counts: RegisterCounts
    students: list[RegisterStudent]


# ---------------------------------------------------------------------------
# Saving marks (same day, or an offline register synced within 72 hours)
# ---------------------------------------------------------------------------
class MarkIn(_Camel):
    student_id: uuid.UUID
    status: Status
    note: str | None = Field(None, max_length=1000)
    # When it was tapped on the phone; the server's clock if left out.
    tapped_at: datetime | None = None


class RegisterSave(_Camel):
    # `register` would shadow BaseModel.register (ABCMeta).
    register_: RegisterKey = Field(alias="register")
    marks: list[MarkIn] = Field(min_length=1, max_length=1000)


class SkippedMark(_Camel):
    student_id: uuid.UUID
    reason: Literal["newer_mark", "not_on_register"]


class RegisterSaveResult(_Camel):
    # `register` would shadow BaseModel.register (ABCMeta).
    register_: RegisterRead = Field(alias="register")
    skipped: list[SkippedMark] = []


# ---------------------------------------------------------------------------
# Today
# ---------------------------------------------------------------------------
class RegisterSlot(_Camel):
    key: RegisterKey
    kind: RegisterKind
    class_name: str
    stream: str | None = None
    subject_name: str | None = None
    starts_at: time | None = None
    ends_at: time | None = None
    teacher_name: str | None = None
    marked: int
    total: int


class NoSchoolDayRead(_Camel):
    id: uuid.UUID
    date: CalendarDate
    reason: str | None = None


class TodayRead(_Camel):
    date: CalendarDate
    today: CalendarDate
    timezone: str
    no_school: NoSchoolDayRead | None = None
    my_lessons: list[RegisterSlot]
    daily: list[RegisterSlot]
    lessons: list[RegisterSlot]
    adhoc: list[RegisterSlot]


# ---------------------------------------------------------------------------
# Corrections
# ---------------------------------------------------------------------------
class CorrectionChange(_Camel):
    student_id: uuid.UUID
    status: Status
    note: str | None = Field(None, max_length=1000)


class CorrectionCreate(_Camel):
    # `register` would shadow BaseModel.register (ABCMeta).
    register_: RegisterKey = Field(alias="register")
    reason: str = Field(min_length=3, max_length=1000)
    changes: list[CorrectionChange] = Field(min_length=1, max_length=1000)


class PersonRead(_Camel):
    id: uuid.UUID | None = None
    name: str | None = None


class CorrectionItemRead(_Camel):
    student_id: uuid.UUID
    student_name: str
    from_status: Status | None = None
    to_status: Status
    note: str | None = None


class RegisterSummary(_Camel):
    register_id: uuid.UUID
    kind: RegisterKind
    date: CalendarDate
    class_name: str
    stream: str | None = None
    subject_name: str | None = None
    starts_at: time | None = None


class CorrectionRequestRead(_Camel):
    id: uuid.UUID
    status: Literal["pending", "accepted", "rejected"]
    reason: str
    # `register` would shadow BaseModel.register (ABCMeta).
    register_: RegisterSummary = Field(alias="register")
    requested_by: PersonRead
    created_at: datetime
    decided_by: PersonRead | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    items: list[CorrectionItemRead]


class CorrectionResult(_Camel):
    """An approver's correction is applied at once; anyone else's becomes a request."""

    applied: bool
    request: CorrectionRequestRead | None = None
    # `register` would shadow BaseModel.register (ABCMeta).
    register_: RegisterRead = Field(alias="register")


class CorrectionDecision(_Camel):
    note: str | None = Field(None, max_length=1000)


class MarkChangeRead(_Camel):
    from_status: Status | None = None
    to_status: Status
    from_note: str | None = None
    to_note: str | None = None
    changed_by: PersonRead
    changed_at: datetime
    tapped_at: datetime
    reason: str | None = None
    correction_request_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# School settings: timezone, term dates, no-school days
# ---------------------------------------------------------------------------
class AttendanceSettingsRead(_Camel):
    timezone: str
    source: Literal["school", "country", "default"]
    country: str | None = None


class AttendanceSettingsUpdate(_Camel):
    """None clears the override, going back to the country's zone."""

    timezone: str | None = Field(None, max_length=64)


class TermDates(_Camel):
    label: str = Field(min_length=1, max_length=100)
    year: int = Field(ge=2000, le=2100)
    starts_on: CalendarDate
    ends_on: CalendarDate

    @model_validator(mode="after")
    def _in_order(self) -> "TermDates":
        if self.ends_on < self.starts_on:
            raise ValueError("A term can't end before it starts")
        return self


class TermDatesWrite(_Camel):
    """Replaces all of the school's term dates."""

    terms: list[TermDates] = Field(max_length=100)


class NoSchoolDayCreate(_Camel):
    date: CalendarDate
    reason: str | None = Field(None, max_length=500)


# ---------------------------------------------------------------------------
# Subjects offered, staff accounts, push tokens
# ---------------------------------------------------------------------------
class SubjectRef(_Camel):
    id: uuid.UUID
    name: str
    code: str


class SubjectChangeRead(_Camel):
    subject_name: str
    action: Literal["added", "removed"]
    changed_by: PersonRead
    changed_at: datetime


class StudentSubjectsRead(_Camel):
    """An empty list means the student takes every subject their class takes."""

    subjects: list[SubjectRef]
    history: list[SubjectChangeRead] = []


class StudentSubjectsWrite(_Camel):
    subject_ids: list[uuid.UUID] = Field(max_length=100)


class StaffAccountWrite(_Camel):
    """None unlinks, going back to matching by email."""

    user_id: uuid.UUID | None = None


class StaffAccountRead(_Camel):
    staff_id: uuid.UUID
    user_id: uuid.UUID | None = None
    linked_by: Literal["admin", "email"] | None = None


class PushTokenRegistration(_Camel):
    token: str = Field(min_length=1, max_length=200)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
class RateRead(_Camel):
    present: int = 0
    late: int = 0
    unexcused: int = 0
    excused: int = 0
    rate: float | None = None


class ClassRates(_Camel):
    class_name: str
    stream: str | None = None
    daily: RateRead
    lessons: RateRead


class OverviewRead(_Camel):
    start: CalendarDate
    end: CalendarDate
    daily: RateRead
    lessons: RateRead
    classes: list[ClassRates]


class SubjectRate(_Camel):
    subject_name: str
    rates: RateRead


class StudentMarkRead(_Camel):
    register_id: uuid.UUID
    # For correcting it; None once its lesson and subject are both deleted.
    key: RegisterKey | None = None
    date: CalendarDate
    kind: RegisterKind
    subject_name: str | None = None
    starts_at: time | None = None
    status: Status
    note: str | None = None


class StudentReportRead(_Camel):
    student_id: uuid.UUID
    name: str
    admission_number: str | None = None
    class_name: str | None = None
    stream: str | None = None
    start: CalendarDate
    end: CalendarDate
    daily: RateRead
    lessons: RateRead
    subjects: list[SubjectRate]
    # Every mark in the range, newest first (at most 500).
    marks: list[StudentMarkRead]


class GapRead(_Camel):
    key: RegisterKey
    date: CalendarDate
    kind: RegisterKind
    class_name: str
    stream: str | None = None
    subject_name: str | None = None
    starts_at: time | None = None
    teacher: PersonRead | None = None
    unmarked: int
    total: int


class GapsRead(_Camel):
    start: CalendarDate
    end: CalendarDate
    # True when the school has no term dates yet, so gaps can't be worked out.
    needs_term_dates: bool
    gaps: list[GapRead]
