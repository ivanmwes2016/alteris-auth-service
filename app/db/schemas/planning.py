"""
Pydantic schemas for lesson planning: schemes of work, lesson plans and the
record of work. camelCase on the wire, like attendance.
"""

import uuid
from datetime import date, datetime, time
from typing import Literal

from pydantic import Field, model_validator

from app.db.schemas.attendance import CalendarDate, PersonRead, _Camel

SchemeStatus = Literal["draft", "submitted", "approved", "sent_back"]
RecordStatus = Literal["taught", "partly_taught", "not_taught"]
NotTaughtReason = Literal["school_event", "teacher_away", "class_away", "other"]

SHORT = 200
LONG = 5000


# ---------------------------------------------------------------------------
# Which lesson
# ---------------------------------------------------------------------------
class LessonKey(_Camel):
    """
    A timetabled lesson: date and lessonId. An extra lesson: date, className,
    optional stream, subjectId, startsAt and minutes.
    """

    date: CalendarDate
    lesson_id: uuid.UUID | None = None
    class_name: str | None = Field(None, min_length=1, max_length=100)
    stream: str | None = Field(None, min_length=1, max_length=100)
    subject_id: uuid.UUID | None = None
    starts_at: time | None = None
    minutes: int | None = Field(None, ge=5, le=600)

    @model_validator(mode="after")
    def _identifies_one_lesson(self) -> "LessonKey":
        if self.lesson_id is None and not (
            self.class_name and self.subject_id and self.starts_at and self.minutes
        ):
            raise ValueError(
                "A lesson needs a lessonId, or className, subjectId, startsAt and minutes"
            )
        return self


# ---------------------------------------------------------------------------
# Attachments and comments
# ---------------------------------------------------------------------------
class UploadUrlRequest(_Camel):
    file_name: str = Field(..., min_length=1, max_length=SHORT)
    content_type: str = Field(..., max_length=100)
    size: int = Field(..., ge=1)


class UploadUrlRead(_Camel):
    path: str
    token: str


class AttachmentIn(_Camel):
    """A file already uploaded to the path an upload URL gave."""

    path: str = Field(..., max_length=500)
    file_name: str = Field(..., min_length=1, max_length=SHORT)
    content_type: str = Field(..., max_length=100)
    size: int = Field(..., ge=1)


class AttachmentRead(_Camel):
    id: uuid.UUID
    file_name: str
    content_type: str
    size: int
    # Signed, for an hour.
    url: str | None


class CommentIn(_Camel):
    body: str = Field(..., min_length=1, max_length=LONG)


class CommentRead(_Camel):
    id: uuid.UUID
    author: PersonRead
    by_approver: bool
    sent_back: bool
    body: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Schemes of work
# ---------------------------------------------------------------------------
class SchemeCreate(_Camel):
    class_name: str = Field(..., min_length=1, max_length=100)
    subject_id: uuid.UUID
    term: str = Field(..., min_length=1, max_length=50)
    year: int = Field(..., ge=2000, le=2100)
    # An earlier scheme for the same class and subject to start from.
    copy_from_id: uuid.UUID | None = None


class EntryIn(_Camel):
    """A scheme entry; with an id it changes that entry, without one it adds one."""

    id: uuid.UUID | None = None
    week: int = Field(..., ge=1, le=60)
    position: int = Field(0, ge=0, le=1000)
    topic: str = Field(..., min_length=1, max_length=SHORT)
    subtopic: str | None = Field(None, max_length=SHORT)
    objectives: str | None = Field(None, max_length=LONG)
    methods: str | None = Field(None, max_length=LONG)
    materials: str | None = Field(None, max_length=LONG)
    references: str | None = Field(None, max_length=LONG)
    lessons: int = Field(1, ge=1, le=50)


class EntriesWrite(_Camel):
    """Every entry of the scheme: ones left out are removed."""

    entries: list[EntryIn] = Field(default_factory=list, max_length=500)


class EntryRead(_Camel):
    id: uuid.UUID
    week: int
    position: int
    topic: str
    subtopic: str | None
    objectives: str | None
    methods: str | None
    materials: str | None
    references: str | None
    lessons: int
    attachments: list[AttachmentRead] = Field(default_factory=list)
    # Added or changed since the scheme was last approved.
    changed_since_approval: bool = False


class TermWeekRead(_Camel):
    week: int
    starts_on: date | None
    ends_on: date | None


class StreamProgress(_Camel):
    stream: str | None
    # The last finished week up to which every entry has a lesson marked Taught.
    covered_through_week: int
    weeks_behind: int
    lessons_taught: int
    lessons_planned_so_far: int


class SchemeSummary(_Camel):
    id: uuid.UUID
    class_name: str
    subject_id: uuid.UUID | None
    subject_name: str
    term: str
    year: int
    status: SchemeStatus
    entry_count: int
    changed_since_approval: bool
    updated_at: datetime


class SchemeRead(SchemeSummary):
    submitted_by: PersonRead | None
    submitted_at: datetime | None
    decided_by: PersonRead | None
    decided_at: datetime | None
    approved_at: datetime | None
    entries: list[EntryRead]
    # Removed since the scheme was last approved: (week, topic).
    removed_since_approval: list[str]
    weeks: list[TermWeekRead]
    # None without term dates, or outside the term.
    current_week: int | None
    needs_term_dates: bool
    progress: list[StreamProgress]


class SendBack(_Camel):
    comment: str = Field(..., min_length=1, max_length=LONG)


# ---------------------------------------------------------------------------
# Lesson plans and the record of work
# ---------------------------------------------------------------------------
class PlanWrite(_Camel):
    """
    The whole plan, sent with the phone's time of the save. The first save
    creates it; later ones replace its content and lessons (latest save wins).
    """

    lessons: list[LessonKey] = Field(..., min_length=1, max_length=10)
    scheme_entry_id: uuid.UUID | None = None
    topic: str = Field(..., min_length=1, max_length=SHORT)
    subtopic: str | None = Field(None, max_length=SHORT)
    objectives: str = Field(..., min_length=1, max_length=LONG)
    materials: str | None = Field(None, max_length=LONG)
    introduction: str | None = Field(None, max_length=LONG)
    development: str | None = Field(None, max_length=LONG)
    assessment: str | None = Field(None, max_length=LONG)
    conclusion: str | None = Field(None, max_length=LONG)
    saved_at: datetime


class RecordWrite(_Camel):
    """A lesson's record of work, with the phone's time it was made."""

    lesson: LessonKey
    status: RecordStatus
    reason: NotTaughtReason | None = None
    comment: str | None = Field(None, max_length=LONG)
    reflection: str | None = Field(None, max_length=LONG)
    recorded_at: datetime

    @model_validator(mode="after")
    def _reason_only_when_not_taught(self) -> "RecordWrite":
        if (self.status == "not_taught") != (self.reason is not None):
            raise ValueError("A reason is needed when, and only when, the lesson wasn't taught")
        return self


class RecordRead(_Camel):
    status: RecordStatus
    reason: NotTaughtReason | None
    comment: str | None
    reflection: str | None
    recorded_by: PersonRead | None
    recorded_at: datetime


class PlannedLessonRead(_Camel):
    id: uuid.UUID
    key: LessonKey
    date: CalendarDate
    class_name: str
    stream: str | None
    subject_name: str
    starts_at: time
    minutes: int
    teacher: PersonRead | None
    record: RecordRead | None


class PlanSummary(_Camel):
    id: uuid.UUID
    class_name: str
    subject_id: uuid.UUID | None
    subject_name: str
    term: str | None
    year: int | None
    week: int | None
    topic: str
    subtopic: str | None
    late: bool
    written_at: datetime
    written_by: PersonRead | None
    lessons: list[PlannedLessonRead]


class PlanRead(PlanSummary):
    scheme_entry_id: uuid.UUID | None
    objectives: str
    materials: str | None
    introduction: str | None
    development: str | None
    assessment: str | None
    conclusion: str | None
    saved_at: datetime
    updated_by: PersonRead | None
    attachments: list[AttachmentRead]
    versions: int


class PlanSaveResult(_Camel):
    plan: PlanRead
    # A newer save was already there, so this one changed nothing.
    stale: bool = False


class PlanVersionRead(_Camel):
    topic: str
    subtopic: str | None
    objectives: str
    materials: str | None
    introduction: str | None
    development: str | None
    assessment: str | None
    conclusion: str | None
    saved_by: PersonRead | None
    saved_at: datetime


# ---------------------------------------------------------------------------
# The week
# ---------------------------------------------------------------------------
class WeekLesson(_Camel):
    key: LessonKey
    class_name: str
    stream: str | None
    subject_id: uuid.UUID | None
    subject_name: str
    starts_at: time
    ends_at: time | None
    minutes: int
    teacher: PersonRead | None
    # The caller teaches it, or added it as an extra lesson.
    mine: bool
    extra: bool
    plan: PlanSummary | None
    record: RecordRead | None


class WeekDay(_Camel):
    date: CalendarDate
    no_school: str | None
    lessons: list[WeekLesson]


class WeekRead(_Camel):
    starts_on: CalendarDate
    today: CalendarDate
    term: str | None
    year: int | None
    week: int | None
    days: list[WeekDay]


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
GapKind = Literal["unplanned", "unrecorded", "late", "taught_no_register", "register_not_taught"]


class PlanningGap(_Camel):
    kind: GapKind
    key: LessonKey
    date: CalendarDate
    class_name: str
    stream: str | None
    subject_name: str
    starts_at: time
    teacher: PersonRead | None
    plan_id: uuid.UUID | None


class PlanningGapsRead(_Camel):
    start: CalendarDate
    end: CalendarDate
    needs_term_dates: bool
    gaps: list[PlanningGap]


class SchemeProgressRead(_Camel):
    scheme_id: uuid.UUID
    class_name: str
    subject_name: str
    status: SchemeStatus
    current_week: int | None
    streams: list[StreamProgress]


class RecordOfWorkRow(_Camel):
    date: CalendarDate
    week: int | None
    stream: str | None
    starts_at: time
    topic: str | None
    subtopic: str | None
    status: RecordStatus | None
    reason: NotTaughtReason | None
    comment: str | None
    teacher: PersonRead | None


class RecordOfWorkRead(_Camel):
    class_name: str
    subject_name: str
    term: str
    year: int
    rows: list[RecordOfWorkRow]
