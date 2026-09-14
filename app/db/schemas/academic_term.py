"""
Pydantic schemas for the academic term settings singleton.

GET/PATCH both operate on the whole settings object directly (not wrapped in
an envelope) — matching the frontend's TermSettings type exactly:
    { labels: string[], currentLabel: string, currentYear: number }
"""

from pydantic import BaseModel, ConfigDict, Field


class TermSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    labels: list[str] = Field(default_factory=list)
    currentLabel: str | None = Field(default=None, validation_alias="current_label")  # noqa: N815
    currentYear: int | None = Field(default=None, validation_alias="current_year")  # noqa: N815


class TermSettingsUpdate(BaseModel):
    labels: list[str] = Field(default_factory=list)
    currentLabel: str | None = None  # noqa: N815
    currentYear: int | None = None  # noqa: N815
