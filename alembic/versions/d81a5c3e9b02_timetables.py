"""timetables: bell schedules, lessons and options periods per term

Revision ID: d81a5c3e9b02
Revises: c4e9a2d7f310
Create Date: 2026-10-26 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d81a5c3e9b02"
down_revision: str | None = "c4e9a2d7f310"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tenant_fk() -> sa.Column:
    return sa.Column(
        "tenant_id", sa.UUID(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )


def _timetable_fk() -> sa.Column:
    return sa.Column(
        "timetable_id",
        sa.UUID(),
        sa.ForeignKey("timetables.id", ondelete="CASCADE"),
        nullable=False,
    )


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "timetables",
        sa.Column("id", sa.UUID(), primary_key=True),
        _tenant_fk(),
        sa.Column("term", sa.String(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("days", postgresql.ARRAY(sa.SmallInteger()), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "year", "term", name="uq_timetable_tenant_year_term"),
    )
    op.create_index("ix_timetables_tenant_id", "timetables", ["tenant_id"])

    op.create_table(
        "timetable_periods",
        sa.Column("id", sa.UUID(), primary_key=True),
        _tenant_fk(),
        _timetable_fk(),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.Time(), nullable=False),
        sa.Column("ends_at", sa.Time(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.CheckConstraint("kind IN ('lesson', 'break')", name="ck_timetable_period_kind"),
        sa.CheckConstraint("starts_at < ends_at", name="ck_timetable_period_times"),
    )
    op.create_index("ix_timetable_periods_timetable_id", "timetable_periods", ["timetable_id"])

    op.create_table(
        "lessons",
        sa.Column("id", sa.UUID(), primary_key=True),
        _tenant_fk(),
        _timetable_fk(),
        sa.Column(
            "class_id",
            sa.UUID(),
            sa.ForeignKey("school_classes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_id",
            sa.UUID(),
            sa.ForeignKey("subjects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("staff_id", sa.UUID(), sa.ForeignKey("staff.id", ondelete="SET NULL")),
        sa.Column("day", sa.SmallInteger(), nullable=False),
        sa.Column(
            "period_id",
            sa.UUID(),
            sa.ForeignKey("timetable_periods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("length", sa.SmallInteger(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("day BETWEEN 0 AND 6", name="ck_lesson_day"),
        sa.CheckConstraint("length >= 1", name="ck_lesson_length"),
    )
    op.create_index("ix_lessons_timetable_id", "lessons", ["timetable_id"])
    op.create_index("ix_lessons_class_id", "lessons", ["class_id"])
    op.create_index("ix_lessons_staff_id", "lessons", ["staff_id"])

    op.create_table(
        "options_periods",
        sa.Column("id", sa.UUID(), primary_key=True),
        _tenant_fk(),
        _timetable_fk(),
        sa.Column(
            "class_id",
            sa.UUID(),
            sa.ForeignKey("school_classes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day", sa.SmallInteger(), nullable=False),
        sa.Column(
            "period_id",
            sa.UUID(),
            sa.ForeignKey("timetable_periods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "class_id", "day", "period_id", name="uq_options_period_class_day_period"
        ),
        sa.CheckConstraint("day BETWEEN 0 AND 6", name="ck_options_period_day"),
    )
    op.create_index("ix_options_periods_timetable_id", "options_periods", ["timetable_id"])


def downgrade() -> None:
    op.drop_table("options_periods")
    op.drop_table("lessons")
    op.drop_table("timetable_periods")
    op.drop_table("timetables")
