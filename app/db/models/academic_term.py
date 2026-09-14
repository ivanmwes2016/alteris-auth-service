import uuid

from sqlalchemy import ForeignKey, Integer, Text
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
