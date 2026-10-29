import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.db.models.attendance import StudentAttendanceProfile
    from app.db.models.behaviour import StudentBehaviourProfile
    from app.db.models.medical_note import StudentMedicalProfile

    from .student_parent import StudentParent


class Student(Base):
    __tablename__ = "students"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    school_id: Mapped[str] = mapped_column(String, nullable=False)

    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=True)
    date_of_birth: Mapped[Date] = mapped_column(Date, nullable=True)
    gender: Mapped[str] = mapped_column(String, nullable=True)
    photo: Mapped[str] = mapped_column(String, nullable=True)
    address: Mapped[str] = mapped_column(String, nullable=True)
    email: Mapped[str] = mapped_column(String, nullable=True)
    phone: Mapped[str] = mapped_column(String, nullable=True)
    class_applied: Mapped[str] = mapped_column(String, nullable=True)
    faculty: Mapped[str] = mapped_column(String, nullable=True)
    course: Mapped[str] = mapped_column(String, nullable=True)
    study_format: Mapped[str] = mapped_column(String, nullable=True)

    created_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now())
    updated_at = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    medical_profile: Mapped[list["StudentMedicalProfile"]] = relationship(
        "StudentMedicalProfile",
        back_populates="student",
        cascade="all, delete-orphan",
        uselist=False,
    )

    behaviour_profile: Mapped[list["StudentBehaviourProfile"]] = relationship(
        "StudentBehaviourProfile",
        back_populates="student",
        cascade="all, delete-orphan",
        uselist=False,
    )

    school = relationship(
        "Tenant",
    )

    parents: Mapped[list["StudentParent"]] = relationship(
        back_populates="student",
        cascade="all, delete-orphan",
    )

    attendance_profile: Mapped["StudentAttendanceProfile"] = relationship(
        "StudentAttendanceProfile",
        back_populates="student",
        cascade="all, delete-orphan",
        uselist=False,
    )


class StudentSubject(Base):
    """
    A subject the student individually takes. A student with none takes every
    subject their class takes, so schools without electives never fill these in.
    """

    __tablename__ = "student_subjects"
    __table_args__ = (
        UniqueConstraint("student_id", "subject_id", name="uq_student_subject"),
        Index("ix_student_subjects_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False
    )


class StudentSubjectChange(Base):
    """History of a student's subjects offered: who added or removed which subject, and when."""

    __tablename__ = "student_subject_changes"
    __table_args__ = (Index("ix_student_subject_changes_student_id", "student_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    subject_name: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)  # "added" | "removed"
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    changed_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
