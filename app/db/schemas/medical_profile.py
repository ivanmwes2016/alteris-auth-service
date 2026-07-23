"""
Pydantic schemas for student medical records.

Matches the normalized model:
    StudentMedicalProfile -> allergies / medications / conditions.
"""

import uuid
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
# Allergy
# ---------------------------------------------------------------------------
class AllergyBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    severity: Severity = Severity.mild
    reaction: str | None = Field(None, max_length=500)


class AllergyCreate(AllergyBase):
    pass


class AllergyUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    severity: Severity | None = None
    reaction: str | None = Field(None, max_length=500)


class AllergyRead(AllergyBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


# ---------------------------------------------------------------------------
# Medication
# ---------------------------------------------------------------------------
class MedicationBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    dosage: str | None = Field(None, max_length=100)
    frequency: str | None = Field(None, max_length=100)
    time: str | None = Field(None, max_length=100)
    prescribed_by: str | None = Field(None, max_length=200)
    is_active: bool = True


class MedicationCreate(MedicationBase):
    pass


class MedicationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    dosage: str | None = Field(None, max_length=100)
    frequency: str | None = Field(None, max_length=100)
    time: str | None = Field(None, max_length=100)
    prescribed_by: str | None = Field(None, max_length=200)
    is_active: bool | None = None


class MedicationRead(MedicationBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


# ---------------------------------------------------------------------------
# Medical condition
# ---------------------------------------------------------------------------
class ConditionBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    notes: str | None = Field(None, max_length=1000)


class ConditionCreate(ConditionBase):
    pass


class ConditionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    notes: str | None = Field(None, max_length=1000)


class ConditionRead(ConditionBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


# ---------------------------------------------------------------------------
# Medical profile (the parent record — blood type, doctor, emergency notes)
# ---------------------------------------------------------------------------
class MedicalProfileBase(BaseModel):
    blood_type: str | None = Field(None, max_length=10)
    emergency_notes: str | None = Field(None, max_length=1000)
    doctor_name: str | None = Field(None, max_length=200)
    doctor_phone: str | None = Field(None, max_length=50)
    clinic_name: str | None = Field(None, max_length=200)


class MedicalProfileUpdate(MedicalProfileBase):
    """All fields optional — this is a PATCH-style partial update."""

    pass


class MedicalProfileRead(MedicalProfileBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    student_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    allergies: list[AllergyRead] = []
    medications: list[MedicationRead] = []
    conditions: list[ConditionRead] = []


# ---------------------------------------------------------------------------
# Bulk sync ("Option B") — one PATCH replaces the profile fields plus whichever
# lists are included. Each list item carries an optional `id`:
#   - id present + matches an existing row  -> update it
#   - id missing (or not found)             -> create it
#   - existing row whose id is NOT in the incoming list -> deleted
# Omit a list key entirely to leave that section untouched.
# ---------------------------------------------------------------------------
class AllergyIn(AllergyBase):
    id: uuid.UUID | None = None


class MedicationIn(MedicationBase):
    id: uuid.UUID | None = None


class ConditionIn(ConditionBase):
    id: uuid.UUID | None = None


class MedicalProfileBulkUpdate(MedicalProfileBase):
    allergies: list[AllergyIn] | None = None
    medications: list[MedicationIn] | None = None
    conditions: list[ConditionIn] | None = None
