import uuid

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    name: Mapped[str] = mapped_column(String)
    plan: Mapped[str] = mapped_column(String, default="free")
    seat_limit: Mapped[int] = mapped_column(default=5)
    stripe_customer_id: Mapped[str | None]
    stripe_subscription_id: Mapped[str | None]