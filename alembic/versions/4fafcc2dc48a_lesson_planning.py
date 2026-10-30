"""lesson planning: schemes of work and their entries, lesson plans with their
versions, planned lessons (which hold the record of work), comments and
attachments.

Revision ID: 4fafcc2dc48a
Revises: f3b8d27c4e19
Create Date: 2026-10-29 21:31:50.815227

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4fafcc2dc48a"
down_revision: str | None = "f3b8d27c4e19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schemes_of_work",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("class_name", sa.String(), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=True),
        sa.Column("subject_name", sa.String(), nullable=False),
        sa.Column("term", sa.String(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("submitted_by", sa.UUID(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_entries", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("copied_from_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'approved', 'sent_back')", name="ck_scheme_status"
        ),
        sa.ForeignKeyConstraint(["copied_from_id"], ["schemes_of_work.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["submitted_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_scheme_of_work",
        "schemes_of_work",
        ["tenant_id", "class_name", "subject_id", "term", "year"],
        unique=True,
        postgresql_where=sa.text("subject_id IS NOT NULL"),
    )
    op.create_table(
        "scheme_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("scheme_id", sa.UUID(), nullable=False),
        sa.Column("week", sa.SmallInteger(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("topic", sa.String(), nullable=False),
        sa.Column("subtopic", sa.String(), nullable=True),
        sa.Column("objectives", sa.Text(), nullable=True),
        sa.Column("methods", sa.Text(), nullable=True),
        sa.Column("materials", sa.Text(), nullable=True),
        sa.Column("references", sa.Text(), nullable=True),
        sa.Column("lessons", sa.SmallInteger(), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("lessons >= 1", name="ck_scheme_entry_lessons"),
        sa.CheckConstraint("week >= 1", name="ck_scheme_entry_week"),
        sa.ForeignKeyConstraint(["scheme_id"], ["schemes_of_work.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scheme_entries_scheme_id", "scheme_entries", ["scheme_id"], unique=False)
    op.create_table(
        "lesson_plans",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("class_name", sa.String(), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=True),
        sa.Column("subject_name", sa.String(), nullable=False),
        sa.Column("term", sa.String(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("week", sa.SmallInteger(), nullable=True),
        sa.Column("scheme_entry_id", sa.UUID(), nullable=True),
        sa.Column("topic", sa.String(), nullable=False),
        sa.Column("subtopic", sa.String(), nullable=True),
        sa.Column("objectives", sa.Text(), nullable=False),
        sa.Column("materials", sa.Text(), nullable=True),
        sa.Column("introduction", sa.Text(), nullable=True),
        sa.Column("development", sa.Text(), nullable=True),
        sa.Column("assessment", sa.Text(), nullable=True),
        sa.Column("conclusion", sa.Text(), nullable=True),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("late", sa.Boolean(), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["scheme_entry_id"], ["scheme_entries.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_lesson_plans_tenant_term",
        "lesson_plans",
        ["tenant_id", "year", "term", "week"],
        unique=False,
    )
    op.create_table(
        "lesson_plan_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("plan_id", sa.UUID(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("saved_by", sa.UUID(), nullable=True),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "replaced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["lesson_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["saved_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_lesson_plan_versions_plan_id", "lesson_plan_versions", ["plan_id"], unique=False
    )
    op.create_table(
        "planned_lessons",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("plan_id", sa.UUID(), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("lesson_id", sa.UUID(), nullable=True),
        sa.Column("class_name", sa.String(), nullable=False),
        sa.Column("stream", sa.String(), nullable=True),
        sa.Column("subject_id", sa.UUID(), nullable=True),
        sa.Column("subject_name", sa.String(), nullable=False),
        sa.Column("starts_at", sa.Time(), nullable=False),
        sa.Column("minutes", sa.SmallInteger(), nullable=False),
        sa.Column("staff_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("record_status", sa.String(), nullable=True),
        sa.Column("not_taught_reason", sa.String(), nullable=True),
        sa.Column("record_comment", sa.Text(), nullable=True),
        sa.Column("reflection", sa.Text(), nullable=True),
        sa.Column("recorded_by", sa.UUID(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(record_status = 'not_taught') = (not_taught_reason IS NOT NULL)",
            name="ck_planned_lesson_reason",
        ),
        sa.CheckConstraint(
            "record_status IS NULL OR record_status IN ('taught', 'partly_taught', 'not_taught')",
            name="ck_planned_lesson_record_status",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["plan_id"], ["lesson_plans.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["recorded_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["staff_id"], ["staff.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_planned_lessons_plan_id", "planned_lessons", ["plan_id"], unique=False)
    op.create_index(
        "ix_planned_lessons_tenant_date", "planned_lessons", ["tenant_id", "date"], unique=False
    )
    op.create_index(
        "uq_planned_lesson_extra",
        "planned_lessons",
        [
            "tenant_id",
            "class_name",
            sa.text("coalesce(stream, '')"),
            "subject_id",
            "date",
            "starts_at",
        ],
        unique=True,
        postgresql_where=sa.text("lesson_id IS NULL"),
    )
    op.create_index(
        "uq_planned_lesson_timetabled",
        "planned_lessons",
        ["lesson_id", "date"],
        unique=True,
        postgresql_where=sa.text("lesson_id IS NOT NULL"),
    )
    op.create_table(
        "planning_attachments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("scheme_entry_id", sa.UUID(), nullable=True),
        sa.Column("plan_id", sa.UUID(), nullable=True),
        sa.Column("storage_path", sa.String(), nullable=False),
        sa.Column("file_name", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(scheme_entry_id IS NULL) <> (plan_id IS NULL)", name="ck_planning_attachment_target"
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["lesson_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scheme_entry_id"], ["scheme_entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_path"),
    )
    op.create_index(
        "ix_planning_attachments_entry_id",
        "planning_attachments",
        ["scheme_entry_id"],
        unique=False,
    )
    op.create_index(
        "ix_planning_attachments_plan_id", "planning_attachments", ["plan_id"], unique=False
    )
    op.create_table(
        "planning_comments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("scheme_id", sa.UUID(), nullable=True),
        sa.Column("plan_id", sa.UUID(), nullable=True),
        sa.Column("author_id", sa.UUID(), nullable=True),
        sa.Column("by_approver", sa.Boolean(), nullable=False),
        sa.Column("sent_back", sa.Boolean(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(scheme_id IS NULL) <> (plan_id IS NULL)", name="ck_planning_comment_target"
        ),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["plan_id"], ["lesson_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scheme_id"], ["schemes_of_work.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_planning_comments_plan_id", "planning_comments", ["plan_id"], unique=False)
    op.create_index(
        "ix_planning_comments_scheme_id", "planning_comments", ["scheme_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_planning_comments_scheme_id", table_name="planning_comments")
    op.drop_index("ix_planning_comments_plan_id", table_name="planning_comments")
    op.drop_table("planning_comments")
    op.drop_index("ix_planning_attachments_plan_id", table_name="planning_attachments")
    op.drop_index("ix_planning_attachments_entry_id", table_name="planning_attachments")
    op.drop_table("planning_attachments")
    op.drop_index(
        "uq_planned_lesson_timetabled",
        table_name="planned_lessons",
        postgresql_where=sa.text("lesson_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_planned_lesson_extra",
        table_name="planned_lessons",
        postgresql_where=sa.text("lesson_id IS NULL"),
    )
    op.drop_index("ix_planned_lessons_tenant_date", table_name="planned_lessons")
    op.drop_index("ix_planned_lessons_plan_id", table_name="planned_lessons")
    op.drop_table("planned_lessons")
    op.drop_index("ix_lesson_plan_versions_plan_id", table_name="lesson_plan_versions")
    op.drop_table("lesson_plan_versions")
    op.drop_index("ix_lesson_plans_tenant_term", table_name="lesson_plans")
    op.drop_table("lesson_plans")
    op.drop_index("ix_scheme_entries_scheme_id", table_name="scheme_entries")
    op.drop_table("scheme_entries")
    op.drop_index(
        "uq_scheme_of_work",
        table_name="schemes_of_work",
        postgresql_where=sa.text("subject_id IS NOT NULL"),
    )
    op.drop_table("schemes_of_work")
