"""
Parents in the app (app.crud.parent_access): the school's invite, accepting it, and
what a parent reads about their own children. camelCase on the wire, like the timetable.
"""

import uuid
from datetime import datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

# not_invited: no login and no invite. invited: an invite is waiting. expired: it ran
# out unused. active: the parent has a login linked to their record.
AppStatus = Literal["not_invited", "invited", "expired", "active"]

# The kinds of push a parent gets, each of which they can turn off.
Topic = Literal["attendance", "results", "health", "behaviour"]
TOPICS: tuple[Topic, ...] = ("attendance", "results", "health", "behaviour")


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


# ---------------------------------------------------------------------------
# The school's side
# ---------------------------------------------------------------------------
class ParentAppAccess(_Camel):
    parent_id: uuid.UUID
    status: AppStatus
    email: str | None = None
    invited_at: datetime | None = None
    invite_expires_at: datetime | None = None
    joined_at: datetime | None = None


# ---------------------------------------------------------------------------
# The invite page (no one is signed in)
# ---------------------------------------------------------------------------
class InviteToken(_Camel):
    token: str = Field(..., min_length=10, max_length=200)


class ParentInvitePreview(_Camel):
    email: str
    parent_name: str
    school_name: str
    # The children's first names, so the parent knows the invite is for them.
    children: list[str]
    # A confirmed login exists for the email: accepting links it, no password asked.
    has_account: bool


class ParentInviteAccept(InviteToken):
    # Needed when there's no confirmed login for the email yet.
    password: str | None = Field(None, max_length=200)


class ParentInviteAccepted(_Camel):
    email: str
    school_name: str
    account_created: bool


# ---------------------------------------------------------------------------
# The parent's side
# ---------------------------------------------------------------------------
class ChildSchool(_Camel):
    id: uuid.UUID
    name: str


class ChildRead(_Camel):
    id: uuid.UUID
    name: str
    admission_number: str | None = None
    class_name: str | None = None
    stream: str | None = None
    relationship_type: str | None = None
    school: ChildSchool


class ChildLessonRead(_Camel):
    id: uuid.UUID
    day: int
    period_id: uuid.UUID
    length: int
    subject_name: str
    teacher_name: str | None = None


class ChildPeriodRead(_Camel):
    id: uuid.UUID
    starts_at: time
    ends_at: time
    kind: Literal["lesson", "break"]
    label: str | None = None


class ChildOptionsPeriodRead(_Camel):
    id: uuid.UUID
    day: int
    period_id: uuid.UUID


class ChildTimetableRead(_Camel):
    """The child's class's lessons this term. `term` is none when no timetable applies."""

    term: str | None = None
    year: int | None = None
    days: list[int] = []
    periods: list[ChildPeriodRead] = []
    lessons: list[ChildLessonRead] = []
    options_periods: list[ChildOptionsPeriodRead] = []


class PushSettingsRead(_Camel):
    attendance: bool = True
    results: bool = True
    health: bool = True
    behaviour: bool = True


class PushSettingsUpdate(_Camel):
    attendance: bool | None = None
    results: bool | None = None
    health: bool | None = None
    behaviour: bool | None = None


# ---------------------------------------------------------------------------
# Term marks published to parents (app.crud.performance_release)
# ---------------------------------------------------------------------------
class ReleaseKey(_Camel):
    """One class's marks for a term, named as the Performance page names them."""

    class_name: str = Field(..., min_length=1, max_length=200)
    term: str = Field(..., min_length=1, max_length=100)
    year: int = Field(..., ge=2000, le=2100)


class ReleaseRead(ReleaseKey):
    released: bool
    released_at: datetime | None = None
    released_by_name: str | None = None
    # Parents told by push when it was published (absent when reading or unpublishing).
    parents_notified: int | None = None


class ChildSubjectScore(_Camel):
    subject: str
    score: str
    # The class's average for the subject, over numeric scores; none if there are none.
    class_average: float | None = None


class ChildTermResults(_Camel):
    term: str
    year: int
    class_name: str
    released_at: datetime
    # The mean of the child's numeric scores; none if they have none.
    average: float | None = None
    scores: list[ChildSubjectScore]
