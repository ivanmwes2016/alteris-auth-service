import uuid

from sqlalchemy import String, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class TenantMember(Base):
    __tablename__ = "tenant_members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id")
    )

    user_id: Mapped[str] = mapped_column(String)

    role: Mapped[str] = mapped_column(String, default="member")

    status: Mapped[str] = mapped_column(String, default="active")