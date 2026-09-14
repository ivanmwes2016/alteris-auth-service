"""
Pydantic schemas for staff qualification records.

Matches the normalized model:
    StaffQualificationProfile -> qualifications.

Note: the frontend's QualificationsTab component wasn't available when this
was written, so the qualification record shape (name/institution/year) is a
best-effort guess at a common "degree/certification" shape. Adjust the fields
below once that component's actual data needs are known.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Qualification
# ---------------------------------------------------------------------------
class QualificationBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    institution: str | None = Field(None, max_length=200)
    year: int | None = None


class QualificationCreate(QualificationBase):
    pass


class QualificationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    institution: str | None = Field(None, max_length=200)
    year: int | None = None


class QualificationRead(QualificationBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


# ---------------------------------------------------------------------------
# Qualification profile (the parent record — free-text notes + qualifications)
# ---------------------------------------------------------------------------
class QualificationProfileBase(BaseModel):
    notes: str | None = Field(None, max_length=2000)


class QualificationProfileRead(QualificationProfileBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    staff_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    qualifications: list[QualificationRead] = []


# ---------------------------------------------------------------------------
# Bulk sync ("Option B") — one PATCH replaces the profile fields plus the
# qualifications list, when included. Each item carries an optional `id`:
#   - id present + matches an existing row  -> update it
#   - id missing (or not found)             -> create it
#   - existing row whose id is NOT in the incoming list -> deleted
# Omit `qualifications` entirely to leave it untouched.
# ---------------------------------------------------------------------------
class QualificationIn(QualificationBase):
    id: uuid.UUID | None = None


class QualificationProfileBulkUpdate(QualificationProfileBase):
    qualifications: list[QualificationIn] | None = None
