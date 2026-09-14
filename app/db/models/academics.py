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
    subject_links: Mapped[list["ClassSubject"]] = relationship(
        back_populates="school_class", cascade="all, delete-orphan", lazy="selectin"
    )


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
    class_links: Mapped[list["ClassSubject"]] = relationship(
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


class ClassSubject(Base):
    """Association: which subjects are taught in which class."""

    __tablename__ = "class_subjects"
    __table_args__ = (
        Index("ix_class_subjects_tenant_id", "tenant_id"),
        UniqueConstraint("class_id", "subject_id", name="uq_class_subject"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_classes.id", ondelete="CASCADE")
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="CASCADE")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )

    school_class: Mapped["SchoolClass"] = relationship(
        "SchoolClass", back_populates="subject_links", lazy="selectin"
    )
    subject: Mapped["Subject"] = relationship("Subject", back_populates="class_links")
