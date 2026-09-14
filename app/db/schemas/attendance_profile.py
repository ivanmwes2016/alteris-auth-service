"""
Pydantic schemas for student attendance records.

Matches the normalized model:
    StudentAttendanceProfile -> records.
"""

import uuid
from datetime import date as date_
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class AttendanceStatus(str, Enum):
    present = "Present"
    late = "Late"
    excused = "Excused"
    unexcused = "Unexcused"


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------
class RecordBase(BaseModel):
    date: date_
    status: AttendanceStatus = AttendanceStatus.present
    note: str | None = Field(None, max_length=1000)


class RecordCreate(RecordBase):
    pass


class RecordUpdate(BaseModel):
    date: date_ | None = None
    status: AttendanceStatus | None = None
    note: str | None = Field(None, max_length=1000)


class RecordRead(RecordBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


# ---------------------------------------------------------------------------
# Attendance profile (the parent record — free-text notes + records)
# ---------------------------------------------------------------------------
class AttendanceProfileBase(BaseModel):
    notes: str | None = Field(None, max_length=2000)


class AttendanceProfileUpdate(AttendanceProfileBase):
    """All fields optional — this is a PATCH-style partial update."""

    pass


class AttendanceProfileRead(AttendanceProfileBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    student_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    records: list[RecordRead] = []


# ---------------------------------------------------------------------------
# Bulk sync ("Option B") — one PATCH replaces the profile fields plus the
# records list, when included. Each record item carries an optional `id`:
#   - id present + matches an existing row  -> update it
#   - id missing (or not found)             -> create it
#   - existing row whose id is NOT in the incoming list -> deleted
# Omit `records` entirely to leave it untouched.
# ---------------------------------------------------------------------------
class RecordIn(RecordBase):
    id: uuid.UUID | None = None


class AttendanceProfileBulkUpdate(AttendanceProfileBase):
    records: list[RecordIn] | None = None
