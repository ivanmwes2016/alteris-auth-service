import uuid

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import mapped_column

from app.core.db import Base


class Enquiry(Base):
    __tablename__ = "enquiries"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)

    name = mapped_column(String, nullable=False)
    class_applied = mapped_column(String, nullable=False)

    parent = mapped_column(String, nullable=True)
    phone = mapped_column(String, nullable=True)
    email = mapped_column(String, nullable=True)
    officer = mapped_column(String, nullable=False)
    previous_school = mapped_column(String, nullable=True)

    stage = mapped_column(String, default="new", nullable=False)
    notes = mapped_column(String, nullable=True)

    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
