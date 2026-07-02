import uuid
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class TenantProfile(Base):
    __tablename__ = "tenant_profiles"

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

    tenant = relationship(
        "Tenant",
        back_populates="profile",
    )

    about: Mapped[str] = mapped_column(Text, nullable=True)
    tagline: Mapped[str] = mapped_column(Text, nullable=True)
    cover_image: Mapped[str] = mapped_column(String, nullable=True)

    courses: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    achievements: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    facilities: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    curriculum: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    subjects: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    levels: Mapped[list] = mapped_column(JSON, nullable=True, default=list)

    type: Mapped[str] = mapped_column(String, nullable=True)
    gender: Mapped[str] = mapped_column(String, nullable=True)
    founded: Mapped[str] = mapped_column(String, nullable=True)
    mode: Mapped[str] = mapped_column(String, nullable=True)

    location: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    contact: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    address: Mapped[str] = mapped_column(String, nullable=True)
    phone: Mapped[str] = mapped_column(String, nullable=True)
    email: Mapped[str] = mapped_column(String, nullable=True)
    website: Mapped[str] = mapped_column(String, nullable=True)
