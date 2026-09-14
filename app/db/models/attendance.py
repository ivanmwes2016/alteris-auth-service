import uuid

from sqlalchemy import Date, DateTime, ForeignKey, Index, Text, func
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
