import uuid

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class TutionFees(Base):
    __tablename__ = "tution_fees"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(String, nullable=False, default="UGX")

    min_fee: Mapped[int | None] = mapped_column(nullable=True)
    max_fee: Mapped[int | None] = mapped_column(nullable=True)

    billing_period: Mapped[str] = mapped_column(String, nullable=False, default="term")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant = relationship("Tenant", back_populates="fees")

    fees = relationship(
        "TutionFees",
        back_populates="tenant",
        uselist=False,
        cascade="all, delete-orphan",
    )
