"""
Pydantic schemas for the Academic Performance page.

`marks` rows are dynamic-keyed objects ({ student, [subject]: score }) on the
frontend — represented here as plain `dict[str, str]` rather than a fixed
model, since subject names aren't known ahead of time.
"""

from pydantic import BaseModel, ConfigDict, Field


class TrendPoint(BaseModel):
    term: str
    average: float


class PerformanceRead(BaseModel):
    subjects: list[str] = Field(default_factory=list)
    marks: list[dict[str, str]] = Field(default_factory=list)
    classAverage: dict[str, float] = Field(default_factory=dict)  # noqa: N815
    trends: list[TrendPoint] = Field(default_factory=list)


class PerformanceUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    class_name: str = Field(..., alias="class", min_length=1)
    term: str = Field(..., min_length=1)
    year: int
    marks: list[dict[str, str]] = Field(default_factory=list)
