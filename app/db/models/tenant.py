import uuid
from sqlalchemy import String, DateTime, func
from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    plan: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=True, default="free")
    logo_path: Mapped[str | None] = mapped_column(String, nullable=True)



    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    members = relationship("TenantMember", back_populates="tenant", cascade="all, delete-orphan",passive_deletes=True)
    invites = relationship("Invite", back_populates="tenant")