"""
The app's Health page: the sick bay (students sent to the nurse) and health
alerts (students whose allergies, conditions or medicines staff should know).
camelCase on the wire, like the timetable. Blank text is stored as none.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from pydantic.alias_generators import to_camel


def _blank_to_none(value: object) -> object:
    return None if isinstance(value, str) and not value.strip() else value


Complaint = Annotated[str, Field(min_length=1, max_length=200)]
LongText = Annotated[Annotated[str, Field(max_length=2000)] | None, BeforeValidator(_blank_to_none)]
VisitStatus = Literal["waiting", "in", "back", "home", "hospital"]


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class VisitCreate(_Camel):
    student_id: uuid.UUID
    complaint: Complaint
    note: LongText = None


class VisitUpdate(_Camel):
    """Only the fields sent change; a blank one is cleared."""

    status: VisitStatus | None = None
    temperature: Annotated[float, Field(ge=30, le=45)] | None = None
    treatment: LongText = None
    nurse_notes: LongText = None


class VisitStudent(_Camel):
    id: uuid.UUID
    name: str
    class_name: str | None
    stream: str | None
    # Allergy names, so whoever treats them sees them first.
    allergies: list[str]


class VisitRead(_Camel):
    id: uuid.UUID
    student: VisitStudent
    day: date
    status: VisitStatus
    complaint: str
    note: str | None
    sent_by_name: str | None
    sent_at: datetime
    temperature: float | None
    treatment: str | None
    nurse_notes: str | None
    arrived_at: datetime | None
    closed_at: datetime | None
    closed_by_name: str | None


class SickBayDay(_Camel):
    """The day's visits, and any still open from earlier days."""

    date: date
    visits: list[VisitRead]


class AlertAllergy(_Camel):
    name: str
    severity: str
    reaction: str | None


class AlertCondition(_Camel):
    name: str
    notes: str | None


class AlertMedication(_Camel):
    name: str
    dosage: str | None
    time: str | None


class HealthAlert(_Camel):
    student_id: uuid.UUID
    name: str
    class_name: str | None
    stream: str | None
    allergies: list[AlertAllergy]
    conditions: list[AlertCondition]
    medications: list[AlertMedication]
    emergency_notes: str | None


class HealthAlerts(_Camel):
    alerts: list[HealthAlert]
