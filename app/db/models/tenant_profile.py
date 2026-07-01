import uuid

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class TenantProfile(Base):
    __tablename__ = "school_profiles"

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

    about: Mapped[str] = mapped_column(Text, nullable=True)
    tagline: Mapped[str] = mapped_column(Text, nullable=True)

    courses = mapped_column(JSON, nullable=True, default=list)
    achievements = mapped_column(JSON, nullable=True, default=list)
    facilities = mapped_column(JSON, nullable=True, default=list)

    address = mapped_column(String, nullable=True)
    phone = mapped_column(String, nullable=True)
    email = mapped_column(String, nullable=True)
    website = mapped_column(String, nullable=True)

    tenant = relationship("Tenant", back_populates="profile")
