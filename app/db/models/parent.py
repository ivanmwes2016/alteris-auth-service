from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .student_parent import StudentParent

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Parent(Base):
    __tablename__ = "parents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    photo: Mapped[str | None] = mapped_column(String, nullable=True)

    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    occupation: Mapped[str | None] = mapped_column(String(100), nullable=True)
    proof_of_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # App access (app.crud.parent_access): the login this parent follows their children
    # with. Set when they accept an invite; never makes them a member of the school.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # The pending invite's token, stored hashed; cleared once it's used.
    invite_token_hash: Mapped[str | None] = mapped_column(String, nullable=True, unique=True)
    invite_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    students: Mapped[list["StudentParent"]] = relationship(  # noqa: UP037
        "StudentParent",
        back_populates="parent",
        cascade="all, delete-orphan",
    )


class ParentPushSettings(Base):
    """Which pushes a parent's login gets. No row means every kind is on."""

    __tablename__ = "parent_push_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    attendance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    results: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    health: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    behaviour: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
