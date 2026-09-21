"""
Schemas for the Users & Roles page.

Responses are camelCase to match the frontend's TeamMember type. Request
bodies reject unknown keys (extra="forbid") so a contract mismatch with the
frontend surfaces as a 422 naming the field instead of being silently dropped.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TeamRole(StrEnum):
    """Roles that can be assigned. "Owner" is deliberately absent — it's never assignable."""

    head_teacher = "Head Teacher"
    admin = "Admin"
    admissions_officer = "Admissions Officer"
    teacher = "Teacher"
    bursar = "Bursar"


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    local, _, domain = email.partition("@")
    if (
        not local
        or "@" in domain
        or "." not in domain
        or domain.startswith(".")
        or domain.endswith(".")
        or any(ch.isspace() for ch in email)
    ):
        raise ValueError("Enter a valid email address")
    return email


class InviteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    role: TeamRole
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)

    @field_validator("email")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("first_name", "last_name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        return " ".join(value.split()) or None if value else None


class RoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: TeamRole


class AcceptInvite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=256)


class TeamMemberRead(BaseModel):
    id: uuid.UUID
    name: str | None = None
    email: str
    role: str
    lastActive: datetime | None = None  # noqa: N815
    status: str


class AcceptInviteResponse(BaseModel):
    tenantId: uuid.UUID  # noqa: N815
    role: str
