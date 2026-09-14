import uuid

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Staff(Base):
    __tablename__ = "staff"
    __table_args__ = (Index("ix_staff_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    title: Mapped[str | None] = mapped_column(String, nullable=True)
    first_name: Mapped[str] = mapped_column(String, nullable=False)
    last_name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    gender: Mapped[str | None] = mapped_column(String, nullable=True)
    date_joined: Mapped["Date"] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="Active")
    role: Mapped[str] = mapped_column(String, nullable=False)
    rank: Mapped[str] = mapped_column(String, nullable=False, default="Basic")
    class_assigned: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant = relationship("Tenant")
    subjects: Mapped[list["StaffSubject"]] = relationship(
        back_populates="staff", cascade="all, delete-orphan", lazy="selectin"
    )
    qualification_profile: Mapped["StaffQualificationProfile"] = relationship(
        "StaffQualificationProfile",
        back_populates="staff",
        cascade="all, delete-orphan",
        uselist=False,
    )


class StaffSubject(Base):
    """A subject name taught by this staff member. Freeform text — no FK to Subject."""

    __tablename__ = "staff_subjects"
    __table_args__ = (Index("ix_staff_subjects_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    staff_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff.id", ondelete="CASCADE")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(String, nullable=False)

    staff = relationship("Staff", back_populates="subjects")


class StaffQualificationProfile(Base):
    __tablename__ = "staff_qualification_profiles"
    __table_args__ = (Index("ix_staff_qualification_profiles_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    staff_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="CASCADE"),
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

    staff: Mapped["Staff"] = relationship("Staff", back_populates="qualification_profile")
    qualifications: Mapped[list["StaffQualification"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", lazy="selectin"
    )


class StaffQualification(Base):
    __tablename__ = "staff_qualifications"
    __table_args__ = (Index("ix_staff_qualifications_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("staff_qualification_profiles.id", ondelete="CASCADE"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    institution: Mapped[str | None] = mapped_column(Text, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    profile = relationship("StaffQualificationProfile", back_populates="qualifications")
