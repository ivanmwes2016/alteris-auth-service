import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class PerformanceRecord(Base):
    """
    One (student, subject) score for a given class/term/year.

    Marks belong to the student by id, so they survive a rename or a class
    change. student_name is the name at the time the mark was saved, kept for
    display and for older marks that couldn't be matched to a student
    (student_id NULL), which are still keyed by name.

    class_name and term_label are freeform display strings, matched the same
    loosely-coupled way the rest of this app matches classes/subjects to
    students (e.g. Subject.classes, SchoolClass.teacher) — not FKs.
    """

    __tablename__ = "performance_records"
    __table_args__ = (
        Index("ix_performance_records_tenant_id", "tenant_id"),
        Index(
            "ix_performance_records_lookup",
            "tenant_id",
            "class_name",
            "term_label",
            "year",
        ),
        UniqueConstraint(
            "tenant_id",
            "class_name",
            "term_label",
            "year",
            "student_id",
            "subject",
            name="uq_performance_record_student",
        ),
        # Unmatched older marks, which have no student_id, stay unique by name.
        Index(
            "uq_performance_record_unmatched_name",
            "tenant_id",
            "class_name",
            "term_label",
            "year",
            "student_name",
            "subject",
            unique=True,
            postgresql_where=text("student_id IS NULL"),
        ),
        Index("ix_performance_records_student_id", "student_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    class_name: Mapped[str] = mapped_column(Text, nullable=False)
    term_label: Mapped[str] = mapped_column(Text, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    student_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=True
    )
    student_name: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant = relationship("Tenant")
