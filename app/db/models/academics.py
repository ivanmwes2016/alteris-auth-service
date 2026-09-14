import uuid

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class SchoolClass(Base):
    __tablename__ = "school_classes"
    __table_args__ = (
        Index("ix_school_classes_tenant_id", "tenant_id"),
        UniqueConstraint("tenant_id", "name", "stream", name="uq_school_class_tenant_name_stream"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    name: Mapped[str] = mapped_column(String, nullable=False)
    stream: Mapped[str | None] = mapped_column(String, nullable=True)
    teacher: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant = relationship("Tenant")


class Subject(Base):
    __tablename__ = "subjects"
    __table_args__ = (
        Index("ix_subjects_tenant_id", "tenant_id"),
        UniqueConstraint("tenant_id", "code", name="uq_subject_tenant_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    name: Mapped[str] = mapped_column(String, nullable=False)
    code: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant = relationship("Tenant")
    teachers: Mapped[list["SubjectTeacher"]] = relationship(
        back_populates="subject", cascade="all, delete-orphan", lazy="selectin"
    )
    classes: Mapped[list["SubjectClass"]] = relationship(
        back_populates="subject", cascade="all, delete-orphan", lazy="selectin"
    )


class SubjectTeacher(Base):
    """A teacher's name attached to a subject. No separate teacher/staff entity exists yet."""

    __tablename__ = "subject_teachers"
    __table_args__ = (Index("ix_subject_teachers_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="CASCADE")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(String, nullable=False)

    subject = relationship("Subject", back_populates="teachers")


class SubjectClass(Base):
    """
    A class display-name string attached to a subject (e.g. "Primary 1 A" —
    SchoolClass.name + " " + SchoolClass.stream). Freeform text matched
    against classDisplayName() client-side, no FK to SchoolClass: the
    frontend's ClassMultiSelect sends/reads plain display-name strings, the
    same way SubjectTeacher stores freeform teacher names.
    """

    __tablename__ = "subject_classes"
    __table_args__ = (Index("ix_subject_classes_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="CASCADE")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(String, nullable=False)

    subject = relationship("Subject", back_populates="classes")
