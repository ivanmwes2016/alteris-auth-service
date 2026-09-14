"""
Pydantic schemas for student behaviour records.

Matches the normalized model:
    StudentBehaviourProfile -> incidents / recognitions / notes.
"""

import uuid
from datetime import date as date_
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class Severity(str, Enum):
    mild = "Mild"
    moderate = "Moderate"
    severe = "Severe"


# ---------------------------------------------------------------------------
# Incident
# ---------------------------------------------------------------------------
class IncidentBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    severity: Severity = Severity.mild
    reporter: str | None = Field(None, max_length=200)
    date: date_ | None = None
    detail: str | None = Field(None, max_length=1000)


class IncidentCreate(IncidentBase):
    pass


class IncidentUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    severity: Severity | None = None
    reporter: str | None = Field(None, max_length=200)
    date: date_ | None = None
    detail: str | None = Field(None, max_length=1000)


class IncidentRead(IncidentBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


# ---------------------------------------------------------------------------
# Recognition
# ---------------------------------------------------------------------------
class RecognitionBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    awarder: str | None = Field(None, max_length=200)
    date: date_ | None = None
    detail: str | None = Field(None, max_length=1000)


class RecognitionCreate(RecognitionBase):
    pass


class RecognitionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    awarder: str | None = Field(None, max_length=200)
    date: date_ | None = None
    detail: str | None = Field(None, max_length=1000)


class RecognitionRead(RecognitionBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


# ---------------------------------------------------------------------------
# Note (staff note)
# ---------------------------------------------------------------------------
class NoteBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    notes: str | None = Field(None, max_length=1000)


class NoteCreate(NoteBase):
    pass


class NoteUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    notes: str | None = Field(None, max_length=1000)


class NoteRead(NoteBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


# ---------------------------------------------------------------------------
# Behaviour profile (the parent record — rating + staff summary notes)
# ---------------------------------------------------------------------------
class BehaviourProfileBase(BaseModel):
    rating: str | None = Field(None, max_length=50)
    summary: str | None = Field(None, max_length=2000)


class BehaviourProfileUpdate(BehaviourProfileBase):
    """All fields optional — this is a PATCH-style partial update."""

    pass


class BehaviourProfileRead(BehaviourProfileBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    student_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    incidents: list[IncidentRead] = []
    recognitions: list[RecognitionRead] = []
    notes: list[NoteRead] = []


# ---------------------------------------------------------------------------
# Bulk sync ("Option B") — one PATCH replaces the profile fields plus whichever
# lists are included. Each list item carries an optional `id`:
#   - id present + matches an existing row  -> update it
#   - id missing (or not found)             -> create it
#   - existing row whose id is NOT in the incoming list -> deleted
# Omit a list key entirely to leave that section untouched.
# ---------------------------------------------------------------------------
class IncidentIn(IncidentBase):
    id: uuid.UUID | None = None


class RecognitionIn(RecognitionBase):
    id: uuid.UUID | None = None


class NoteIn(NoteBase):
    id: uuid.UUID | None = None


class BehaviourProfileBulkUpdate(BehaviourProfileBase):
    incidents: list[IncidentIn] | None = None
    recognitions: list[RecognitionIn] | None = None
    notes: list[NoteIn] | None = None
