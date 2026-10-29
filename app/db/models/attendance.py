import uuid
from datetime import date as date_
from datetime import datetime, time

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.db.models.student import Student
from app.db.models.tenant import Tenant


class StudentAttendanceProfile(Base):
    __tablename__ = "student_attendance_profiles"
    __table_args__ = (Index("ix_attendance_profiles_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("students.id", ondelete="CASCADE"),
        unique=True,
        nullable=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    student: Mapped["Student"] = relationship("Student", back_populates="attendance_profile")
    tenant: Mapped["Tenant"] = relationship("Tenant")

    records: Mapped[list["StudentAttendanceRecord"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", lazy="selectin"
    )


class StudentAttendanceRecord(Base):
    __tablename__ = "student_attendance_records"
    __table_args__ = (Index("ix_attendance_records_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("student_attendance_profiles.id", ondelete="CASCADE"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )

    date: Mapped["Date"] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="Present")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    profile = relationship("StudentAttendanceProfile", back_populates="records")


# ---------------------------------------------------------------------------
# Registers (replace the per-student profile above, which is kept read-only
# until the web tab moves over; its rows were copied into daily registers).
# ---------------------------------------------------------------------------
class AttendanceRegister(Base):
    """
    One register: a class's daily roll call, or one lesson of one subject, on
    one school day. A timetabled lesson's register points at its Lesson; an
    ad-hoc one is identified by its class, stream, subject and start time.
    A daily register is the whole class, so a stream only filters the list.
    """

    __tablename__ = "attendance_registers"
    __table_args__ = (
        Index("ix_attendance_registers_tenant_date", "tenant_id", "date"),
        Index(
            "uq_attendance_register_daily",
            "tenant_id",
            "class_name",
            "date",
            unique=True,
            postgresql_where=text("kind = 'daily'"),
        ),
        Index(
            "uq_attendance_register_lesson",
            "lesson_id",
            "date",
            unique=True,
            postgresql_where=text("lesson_id IS NOT NULL"),
        ),
        Index(
            "uq_attendance_register_adhoc",
            "tenant_id",
            "class_name",
            text("coalesce(stream, '')"),
            "subject_id",
            "date",
            "starts_at",
            unique=True,
            postgresql_where=text("kind = 'lesson' AND lesson_id IS NULL"),
        ),
        CheckConstraint("kind IN ('daily', 'lesson')", name="ck_attendance_register_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    date: Mapped[date_] = mapped_column(Date, nullable=False)
    class_name: Mapped[str] = mapped_column(String, nullable=False)
    stream: Mapped[str | None] = mapped_column(String, nullable=True)
    # Kept by name too, so renaming or deleting a subject doesn't rewrite past attendance.
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True
    )
    subject_name: Mapped[str | None] = mapped_column(String, nullable=True)
    lesson_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lessons.id", ondelete="SET NULL"), nullable=True
    )
    starts_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    # Who answers for its register gaps: the timetabled teacher, as they were that day.
    staff_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    marks: Mapped[list["AttendanceMark"]] = relationship(
        back_populates="register", cascade="all, delete-orphan", lazy="selectin"
    )


class AttendanceMark(Base):
    """One student's status on one register. No row means Unmarked."""

    __tablename__ = "attendance_marks"
    __table_args__ = (
        UniqueConstraint("register_id", "student_id", name="uq_attendance_mark_register_student"),
        Index("ix_attendance_marks_tenant_student", "tenant_id", "student_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    register_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("attendance_registers.id", ondelete="CASCADE"),
        nullable=False,
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The student's class and stream when the mark was taken; promotions don't rewrite it.
    class_name: Mapped[str] = mapped_column(String, nullable=False)
    stream: Mapped[str | None] = mapped_column(String, nullable=True)
    marked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # When it was tapped on the phone: the latest tap wins (docs/adr/0001 in the app repo).
    tapped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    register: Mapped["AttendanceRegister"] = relationship(back_populates="marks")


class AttendanceMarkChange(Base):
    """Every change to a mark: from (None = Unmarked) and to, by whom, when and why."""

    __tablename__ = "attendance_mark_changes"
    __table_args__ = (Index("ix_attendance_mark_changes_mark_id", "mark_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    mark_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attendance_marks.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String, nullable=True)
    to_status: Mapped[str] = mapped_column(String, nullable=False)
    from_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    tapped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Set for corrections (and accepted correction requests), empty for same-day edits.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    correction_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("attendance_correction_requests.id", ondelete="SET NULL"),
        nullable=True,
    )


class CorrectionRequest(Base):
    """A staff member's ask to change a past register. Marks stay as they are until accepted."""

    __tablename__ = "attendance_correction_requests"
    __table_args__ = (
        Index("ix_attendance_correction_requests_tenant_status", "tenant_id", "status"),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')", name="ck_correction_request_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    register_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("attendance_registers.id", ondelete="CASCADE"),
        nullable=False,
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    register: Mapped["AttendanceRegister"] = relationship(lazy="selectin")
    items: Mapped[list["CorrectionRequestItem"]] = relationship(
        back_populates="request", cascade="all, delete-orphan", lazy="selectin"
    )


class CorrectionRequestItem(Base):
    """One student's change in a correction request; from_status None means they were Unmarked."""

    __tablename__ = "attendance_correction_request_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("attendance_correction_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String, nullable=True)
    to_status: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    request: Mapped["CorrectionRequest"] = relationship(back_populates="items")


class NoSchoolDay(Base):
    """A date within a term when the school is closed: no register gaps that day."""

    __tablename__ = "no_school_days"
    __table_args__ = (UniqueConstraint("tenant_id", "date", name="uq_no_school_day_tenant_date"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date_] = mapped_column(Date, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
