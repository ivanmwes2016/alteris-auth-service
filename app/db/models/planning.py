"""
Lesson planning: schemes of work, lesson plans and the record of work.

A scheme of work is one subject for one class name (all its streams) in one
term, split into scheme entries by term week. A lesson plan serves one or more
lessons (a timetabled Lesson on a date, or an extra lesson); each of those
lessons is a planned lesson, which also holds its record of work. A planned
lesson can exist without a plan: a lesson that didn't happen is recorded Not
taught without anyone planning it.

Subjects are kept by name too, like registers, so renaming or deleting one
doesn't rewrite past plans.
"""

import uuid
from datetime import date as date_
from datetime import datetime, time
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

SCHEME_STATUSES = ("draft", "submitted", "approved", "sent_back")
RECORD_STATUSES = ("taught", "partly_taught", "not_taught")
NOT_TAUGHT_REASONS = ("school_event", "teacher_away", "class_away", "other")


class SchemeOfWork(Base):
    __tablename__ = "schemes_of_work"
    __table_args__ = (
        Index(
            "uq_scheme_of_work",
            "tenant_id",
            "class_name",
            "subject_id",
            "term",
            "year",
            unique=True,
            postgresql_where=text("subject_id IS NOT NULL"),
        ),
        CheckConstraint(
            "status IN ('draft', 'submitted', 'approved', 'sent_back')", name="ck_scheme_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    # The class name without its stream (e.g. "S.2"): every stream shares the scheme.
    class_name: Mapped[str] = mapped_column(String, nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True
    )
    subject_name: Mapped[str] = mapped_column(String, nullable=False)
    term: Mapped[str] = mapped_column(String, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The entries as they were when last approved, to show what changed since.
    approved_entries: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    copied_from_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schemes_of_work.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    entries: Mapped[list["SchemeEntry"]] = relationship(
        back_populates="scheme",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="(SchemeEntry.week, SchemeEntry.position)",
    )


class SchemeEntry(Base):
    """One sub-topic in a term week of a scheme."""

    __tablename__ = "scheme_entries"
    __table_args__ = (
        Index("ix_scheme_entries_scheme_id", "scheme_id"),
        CheckConstraint("week >= 1", name="ck_scheme_entry_week"),
        CheckConstraint("lessons >= 1", name="ck_scheme_entry_lessons"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    scheme_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schemes_of_work.id", ondelete="CASCADE"), nullable=False
    )
    week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    topic: Mapped[str] = mapped_column(String, nullable=False)
    subtopic: Mapped[str | None] = mapped_column(String, nullable=True)
    objectives: Mapped[str | None] = mapped_column(Text, nullable=True)
    methods: Mapped[str | None] = mapped_column(Text, nullable=True)
    materials: Mapped[str | None] = mapped_column(Text, nullable=True)
    references: Mapped[str | None] = mapped_column(Text, nullable=True)
    # How many lessons the sub-topic should take.
    lessons: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)

    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    scheme: Mapped[SchemeOfWork] = relationship(back_populates="entries")


class LessonPlan(Base):
    """
    The plan for one or more lessons of one subject and class name in one term
    week. Its id is chosen by the phone, so a plan written offline and sent
    twice is still one plan.
    """

    __tablename__ = "lesson_plans"
    __table_args__ = (Index("ix_lesson_plans_tenant_term", "tenant_id", "year", "term", "week"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    class_name: Mapped[str] = mapped_column(String, nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True
    )
    subject_name: Mapped[str] = mapped_column(String, nullable=False)
    term: Mapped[str | None] = mapped_column(String, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    week: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # Where it was copied from; the copy stands on its own.
    scheme_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scheme_entries.id", ondelete="SET NULL"), nullable=True
    )

    topic: Mapped[str] = mapped_column(String, nullable=False)
    subtopic: Mapped[str | None] = mapped_column(String, nullable=True)
    objectives: Mapped[str] = mapped_column(Text, nullable=False)
    materials: Mapped[str | None] = mapped_column(Text, nullable=True)
    introduction: Mapped[str | None] = mapped_column(Text, nullable=True)
    development: Mapped[str | None] = mapped_column(Text, nullable=True)
    assessment: Mapped[str | None] = mapped_column(Text, nullable=True)
    conclusion: Mapped[str | None] = mapped_column(Text, nullable=True)

    # When the teacher first wrote it, by the phone's clock (docs/adr/0001).
    written_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Written after its first lesson started.
    late: Mapped[bool] = mapped_column(nullable=False, default=False)
    # The latest save's phone time: an older offline copy never overwrites a newer one.
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    lessons: Mapped[list["PlannedLesson"]] = relationship(
        back_populates="plan",
        lazy="selectin",
        order_by="(PlannedLesson.date, PlannedLesson.starts_at)",
    )


class LessonPlanVersion(Base):
    """A lesson plan's content before a save changed it."""

    __tablename__ = "lesson_plan_versions"
    __table_args__ = (Index("ix_lesson_plan_versions_plan_id", "plan_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lesson_plans.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    saved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    replaced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PlannedLesson(Base):
    """
    One lesson that has a plan, a record of work, or both: a timetabled Lesson
    on a date, or an extra lesson (class, stream, subject and start time).
    """

    __tablename__ = "planned_lessons"
    __table_args__ = (
        Index("ix_planned_lessons_tenant_date", "tenant_id", "date"),
        Index("ix_planned_lessons_plan_id", "plan_id"),
        Index(
            "uq_planned_lesson_timetabled",
            "lesson_id",
            "date",
            unique=True,
            postgresql_where=text("lesson_id IS NOT NULL"),
        ),
        Index(
            "uq_planned_lesson_extra",
            "tenant_id",
            "class_name",
            text("coalesce(stream, '')"),
            "subject_id",
            "date",
            "starts_at",
            unique=True,
            postgresql_where=text("lesson_id IS NULL"),
        ),
        CheckConstraint(
            "record_status IS NULL OR record_status IN ('taught', 'partly_taught', 'not_taught')",
            name="ck_planned_lesson_record_status",
        ),
        CheckConstraint(
            "(record_status = 'not_taught') = (not_taught_reason IS NOT NULL)",
            name="ck_planned_lesson_reason",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lesson_plans.id", ondelete="SET NULL"), nullable=True
    )
    date: Mapped[date_] = mapped_column(Date, nullable=False)
    # A deleted timetable lesson leaves an extra lesson behind, like registers.
    lesson_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lessons.id", ondelete="SET NULL"), nullable=True
    )
    class_name: Mapped[str] = mapped_column(String, nullable=False)
    stream: Mapped[str | None] = mapped_column(String, nullable=True)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True
    )
    subject_name: Mapped[str] = mapped_column(String, nullable=False)
    starts_at: Mapped[time] = mapped_column(Time, nullable=False)
    minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # Who answers for it: the timetabled teacher when planned, else whoever added the extra lesson.
    staff_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # The record of work.
    record_status: Mapped[str | None] = mapped_column(String, nullable=True)
    not_taught_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    record_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reflection: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # By the phone's clock: the latest record wins.
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    plan: Mapped[LessonPlan | None] = relationship(back_populates="lessons")


class PlanningComment(Base):
    """A comment in a scheme's or a lesson plan's thread."""

    __tablename__ = "planning_comments"
    __table_args__ = (
        Index("ix_planning_comments_scheme_id", "scheme_id"),
        Index("ix_planning_comments_plan_id", "plan_id"),
        CheckConstraint(
            "(scheme_id IS NULL) <> (plan_id IS NULL)", name="ck_planning_comment_target"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    scheme_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schemes_of_work.id", ondelete="CASCADE"), nullable=True
    )
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lesson_plans.id", ondelete="CASCADE"), nullable=True
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Written by an approver, shown as such.
    by_approver: Mapped[bool] = mapped_column(nullable=False, default=False)
    # Set on the comment that sent a scheme back.
    sent_back: Mapped[bool] = mapped_column(nullable=False, default=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlanningAttachment(Base):
    """A photo or PDF on a scheme entry or a lesson plan, kept in private storage."""

    __tablename__ = "planning_attachments"
    __table_args__ = (
        Index("ix_planning_attachments_entry_id", "scheme_entry_id"),
        Index("ix_planning_attachments_plan_id", "plan_id"),
        CheckConstraint(
            "(scheme_entry_id IS NULL) <> (plan_id IS NULL)", name="ck_planning_attachment_target"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    scheme_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scheme_entries.id", ondelete="CASCADE"), nullable=True
    )
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lesson_plans.id", ondelete="CASCADE"), nullable=True
    )
    storage_path: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    file_name: Mapped[str] = mapped_column(String, nullable=False)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
