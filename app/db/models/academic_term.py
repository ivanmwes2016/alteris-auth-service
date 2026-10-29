import uuid
from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class AcademicTermSettings(Base):
    """
    One row per tenant holding the school's term labels and which one is
    current — a singleton settings object, not a list of dated records.
    """

    __tablename__ = "academic_term_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    labels: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    current_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    tenant = relationship("Tenant")


class AcademicTermDates(Base):
    """When one of the school's terms runs, e.g. Term II 2026: 26 May to 22 Aug."""

    __tablename__ = "academic_term_dates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "year", "label", name="uq_academic_term_dates_year_label"),
        CheckConstraint("ends_on >= starts_on", name="ck_academic_term_dates_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
