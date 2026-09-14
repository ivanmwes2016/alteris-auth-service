import uuid

from sqlalchemy import Date, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.db.models.student import Student
from app.db.models.tenant import Tenant


class StudentBehaviourProfile(Base):
    __tablename__ = "student_behaviour_profiles"
    __table_args__ = (Index("ix_behaviour_profiles_tenant_id", "tenant_id"),)

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

    rating: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    student: Mapped["Student"] = relationship("Student", back_populates="behaviour_profile")
    tenant: Mapped["Tenant"] = relationship("Tenant")

    incidents: Mapped[list["BehaviourIncident"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", lazy="selectin"
    )
    recognitions: Mapped[list["BehaviourRecognition"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", lazy="selectin"
    )
    notes: Mapped[list["BehaviourNote"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", lazy="selectin"
    )


class BehaviourIncident(Base):
    __tablename__ = "student_behaviour_incidents"
    __table_args__ = (Index("ix_behaviour_incidents_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("student_behaviour_profiles.id", ondelete="CASCADE"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False, default="Mild")
    reporter: Mapped[str | None] = mapped_column(Text, nullable=True)
    date: Mapped["Date"] = mapped_column(Date, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    profile = relationship("StudentBehaviourProfile", back_populates="incidents")


class BehaviourRecognition(Base):
    __tablename__ = "student_behaviour_recognitions"
    __table_args__ = (Index("ix_behaviour_recognitions_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("student_behaviour_profiles.id", ondelete="CASCADE"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    awarder: Mapped[str | None] = mapped_column(Text, nullable=True)
    date: Mapped["Date"] = mapped_column(Date, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    profile = relationship("StudentBehaviourProfile", back_populates="recognitions")


class BehaviourNote(Base):
    __tablename__ = "student_behaviour_notes"
    __table_args__ = (Index("ix_behaviour_notes_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("student_behaviour_profiles.id", ondelete="CASCADE"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    profile = relationship("StudentBehaviourProfile", back_populates="notes")
