import uuid
from datetime import date as date_
from datetime import datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class SickBayVisit(Base):
    """
    A student sent to the sick bay: a teacher sends them with what's wrong, the
    nurse records what was done and where they went next (app.crud.sick_bay).
    """

    __tablename__ = "sick_bay_visits"
    __table_args__ = (
        Index("ix_sick_bay_visits_tenant_day", "tenant_id", "day"),
        Index("ix_sick_bay_visits_student_id", "student_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    # The school's date the student was sent, in the school's timezone.
    day: Mapped[date_] = mapped_column(Date, nullable=False)
    # waiting -> in -> back | home | hospital (app.crud.sick_bay.STATUSES)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="waiting")

    complaint: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_by_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    treatment: Mapped[str | None] = mapped_column(Text, nullable=True)
    nurse_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by_name: Mapped[str | None] = mapped_column(Text, nullable=True)
