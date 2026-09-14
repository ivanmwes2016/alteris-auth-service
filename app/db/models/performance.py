import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class PerformanceRecord(Base):
    """
    One (student, subject) score for a given class/term/year.

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
            "student_name",
            "subject",
            name="uq_performance_record",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    class_name: Mapped[str] = mapped_column(Text, nullable=False)
    term_label: Mapped[str] = mapped_column(Text, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
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
