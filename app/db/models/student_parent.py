import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.db.models.parent import Parent
from app.db.models.student import Student


class StudentParent(Base):
    __tablename__ = "student_parents"

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("students.id", ondelete="CASCADE"),
        primary_key=True,
    )

    parent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("parents.id", ondelete="CASCADE"),
        primary_key=True,
    )

    relationship_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    # e.g. "father", "mother", "guardian", "uncle", "aunt"

    is_primary_contact: Mapped[bool] = mapped_column(default=False)
    can_pick_up: Mapped[bool] = mapped_column(default=True)

    student: Mapped[Student] = relationship(back_populates="parents")
    parent: Mapped[Parent] = relationship(back_populates="students")

    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "parent_id",
            name="uq_student_parent",
        ),
    )
