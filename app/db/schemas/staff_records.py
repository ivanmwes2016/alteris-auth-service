"""
The Documents and Health tabs on a staff profile. Each tab saves its whole
record in one PATCH, the shape the School Hub sends (camelCase). An item's `id`
works as for qualifications: one that matches is updated, any other is a new
item, and an item left out is deleted. Blank text and dates are stored as none.
"""

import uuid
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from pydantic.alias_generators import to_camel


def _blank_to_none(value: object) -> object:
    return None if isinstance(value, str) and not value.strip() else value


Name = Annotated[str, Field(min_length=1, max_length=200)]
LongText = Annotated[Annotated[str, Field(max_length=2000)] | None, BeforeValidator(_blank_to_none)]
ShortText = Annotated[Annotated[str, Field(max_length=200)] | None, BeforeValidator(_blank_to_none)]
OptionalDate = Annotated[date | None, BeforeValidator(_blank_to_none)]
Severity = Literal["Mild", "Moderate", "Severe"]
MAX_ITEMS = 100


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
class StaffDocumentIn(_Camel):
    id: uuid.UUID | None = None
    name: Name
    category: Name
    date: OptionalDate = None
    notes: LongText = None


class StaffDocumentRead(StaffDocumentIn):
    id: uuid.UUID


class StaffDocumentsWrite(_Camel):
    documents: list[StaffDocumentIn] = Field(max_length=MAX_ITEMS)


class StaffDocumentsRead(_Camel):
    documents: list[StaffDocumentRead]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
class ConditionIn(_Camel):
    id: uuid.UUID | None = None
    name: Name
    notes: LongText = None


class ConditionRead(ConditionIn):
    id: uuid.UUID


class AllergyIn(_Camel):
    id: uuid.UUID | None = None
    name: Name
    severity: Severity = "Mild"
    reaction: LongText = None


class AllergyRead(AllergyIn):
    id: uuid.UUID


class StaffHealthWrite(_Camel):
    """Fields left out are left as they are; a list sent replaces the one saved."""

    emergency_contact: ShortText = None
    emergency_phone: ShortText = None
    notes: LongText = None
    conditions: list[ConditionIn] | None = Field(None, max_length=MAX_ITEMS)
    allergies: list[AllergyIn] | None = Field(None, max_length=MAX_ITEMS)


class StaffHealthRead(_Camel):
    emergency_contact: str | None = None
    emergency_phone: str | None = None
    notes: str | None = None
    conditions: list[ConditionRead] = []
    allergies: list[AllergyRead] = []
