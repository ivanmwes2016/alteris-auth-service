"""
A school's timetable for one term: its bell schedule (periods and breaks, the
same every school day), the school days it covers, its lessons and the
options periods where a class may hold several lessons at once.

Lessons reference their class, subject and staff member by id, not by name
(unlike subject_classes / subject_teachers): clash detection needs to know
when two lessons share a teacher or a class. See docs/adr/0001 in the UI repo.
"""

import uuid
from datetime import time

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Timetable(Base):
    __tablename__ = "timetables"
    __table_args__ = (
        Index("ix_timetables_tenant_id", "tenant_id"),
        UniqueConstraint("tenant_id", "year", "term", name="uq_timetable_tenant_year_term"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    # A term label from the school's academic term settings (e.g. "Term II").
    term: Mapped[str] = mapped_column(String, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    # School days as 0 = Monday … 6 = Sunday, ascending.
    days: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger), nullable=False)

    created_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    periods: Mapped[list["TimetablePeriod"]] = relationship(
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="TimetablePeriod.position",
    )
    lessons: Mapped[list["Lesson"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    options_periods: Mapped[list["OptionsPeriod"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class TimetablePeriod(Base):
    """One block of the bell schedule. `kind` is "lesson" or "break"; breaks have a label."""

    __tablename__ = "timetable_periods"
    __table_args__ = (
        Index("ix_timetable_periods_timetable_id", "timetable_id"),
        CheckConstraint("kind IN ('lesson', 'break')", name="ck_timetable_period_kind"),
        CheckConstraint("starts_at < ends_at", name="ck_timetable_period_times"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    timetable_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timetables.id", ondelete="CASCADE"), nullable=False
    )

    position: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[time] = mapped_column(Time, nullable=False)
    ends_at: Mapped[time] = mapped_column(Time, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False, default="lesson")
    label: Mapped[str | None] = mapped_column(String, nullable=True)


class Lesson(Base):
    """
    One subject for one class on one school day, starting in `period_id` and
    running `length` back-to-back teaching periods. The teacher is optional.
    """

    __tablename__ = "lessons"
    __table_args__ = (
        Index("ix_lessons_timetable_id", "timetable_id"),
        Index("ix_lessons_class_id", "class_id"),
        Index("ix_lessons_staff_id", "staff_id"),
        CheckConstraint("day BETWEEN 0 AND 6", name="ck_lesson_day"),
        CheckConstraint("length >= 1", name="ck_lesson_length"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    timetable_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timetables.id", ondelete="CASCADE"), nullable=False
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_classes.id", ondelete="CASCADE"), nullable=False
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False
    )
    # Deleting a staff member leaves their lessons unassigned.
    staff_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff.id", ondelete="SET NULL"), nullable=True
    )

    day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timetable_periods.id", ondelete="CASCADE"), nullable=False
    )
    length: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)

    created_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OptionsPeriod(Base):
    """A class's period on one day where several lessons run side by side (not a clash)."""

    __tablename__ = "options_periods"
    __table_args__ = (
        Index("ix_options_periods_timetable_id", "timetable_id"),
        UniqueConstraint("class_id", "day", "period_id", name="uq_options_period_class_day_period"),
        CheckConstraint("day BETWEEN 0 AND 6", name="ck_options_period_day"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    timetable_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timetables.id", ondelete="CASCADE"), nullable=False
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_classes.id", ondelete="CASCADE"), nullable=False
    )
    day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timetable_periods.id", ondelete="CASCADE"), nullable=False
    )
