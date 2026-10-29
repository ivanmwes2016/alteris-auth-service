import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
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
    plan: Mapped[str] = mapped_column(String, index=True, nullable=True, default="free")
    seat_limit: Mapped[int] = mapped_column(Integer, nullable=True, default=15)
    stripe_customer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String, nullable=True)
    logo_path: Mapped[str | None] = mapped_column(String, nullable=True)
    country: Mapped[str | None] = mapped_column(String, nullable=True)
    # IANA zone the school day is measured in, e.g. "Africa/Kampala". None: worked out
    # from the country (app/helpers/timezones.py); set when someone overrides it, or at
    # sign-up from the registering device in countries that span several zones.
    timezone: Mapped[str | None] = mapped_column(String, nullable=True)
    # From the sign-up form, for the platform team to contact and understand the school.
    # Required for new schools; older schools don't have them.
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    school_type: Mapped[str | None] = mapped_column(String, nullable=True)
    referral_source: Mapped[str | None] = mapped_column(String, nullable=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # A new school waits for approval by the platform team (an emailed link) before
    # anyone can use it. Rows written outside the app (and every school that existed
    # before approvals) default to approved; the app creates new schools as pending.
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending", server_default="approved"
    )
    approved_at = mapped_column(DateTime(timezone=True), nullable=True)
    # Free trial, counted from approval. None: never on a trial (e.g. older schools).
    trial_ends_at = mapped_column(DateTime(timezone=True), nullable=True)
    # SHA-256 of the one-time approve/reject link token; cleared once used.
    review_token_hash: Mapped[str | None] = mapped_column(String, nullable=True, unique=True)

    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    members = relationship(
        "TenantMember",
        back_populates="tenant",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    invites = relationship("Invite", back_populates="tenant")

    enquiries = relationship(
        "Enquiry",
        back_populates="tenant",
        cascade="all, delete-orphan",
    )

    profile = relationship(
        "TenantProfile",
        back_populates="tenant",
        uselist=False,
        cascade="all, delete-orphan",
    )

    fees = relationship(
        "TutionFees",
        back_populates="tenant",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
