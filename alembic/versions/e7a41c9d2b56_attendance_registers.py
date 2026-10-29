"""attendance registers: daily and lesson registers, marks with history, correction
requests, no-school days, term dates, subjects offered, staff accounts, school
timezone and push tokens. Copies the old per-student records into daily registers.

Revision ID: e7a41c9d2b56
Revises: d81a5c3e9b02
Create Date: 2026-10-29 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7a41c9d2b56"
down_revision: str | None = "d81a5c3e9b02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tenant_fk() -> sa.Column:
    return sa.Column(
        "tenant_id", sa.UUID(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )


def _user_fk(name: str, *, nullable: bool = True, ondelete: str = "SET NULL") -> sa.Column:
    return sa.Column(name, sa.UUID(), sa.ForeignKey("users.id", ondelete=ondelete), nullable=nullable)


def _now(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.add_column("tenants", sa.Column("timezone", sa.String(), nullable=True))

    op.add_column("staff", _user_fk("user_id"))
    op.create_unique_constraint("uq_staff_tenant_user", "staff", ["tenant_id", "user_id"])

    op.create_table(
        "academic_term_dates",
        sa.Column("id", sa.UUID(), primary_key=True),
        _tenant_fk(),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.UniqueConstraint("tenant_id", "year", "label", name="uq_academic_term_dates_year_label"),
        sa.CheckConstraint("ends_on >= starts_on", name="ck_academic_term_dates_order"),
    )

    op.create_table(
        "student_subjects",
        sa.Column("id", sa.UUID(), primary_key=True),
        _tenant_fk(),
        sa.Column(
            "student_id", sa.UUID(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "subject_id", sa.UUID(), sa.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.UniqueConstraint("student_id", "subject_id", name="uq_student_subject"),
    )
    op.create_index("ix_student_subjects_tenant_id", "student_subjects", ["tenant_id"])

    op.create_table(
        "student_subject_changes",
        sa.Column("id", sa.UUID(), primary_key=True),
        _tenant_fk(),
        sa.Column(
            "student_id", sa.UUID(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("subject_name", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        _user_fk("changed_by"),
        _now("changed_at"),
    )
    op.create_index(
        "ix_student_subject_changes_student_id", "student_subject_changes", ["student_id"]
    )

    op.create_table(
        "attendance_registers",
        sa.Column("id", sa.UUID(), primary_key=True),
        _tenant_fk(),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("class_name", sa.String(), nullable=False),
        sa.Column("stream", sa.String(), nullable=True),
        sa.Column(
            "subject_id", sa.UUID(), sa.ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("subject_name", sa.String(), nullable=True),
        sa.Column(
            "lesson_id", sa.UUID(), sa.ForeignKey("lessons.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("starts_at", sa.Time(), nullable=True),
        sa.Column("staff_id", sa.UUID(), sa.ForeignKey("staff.id", ondelete="SET NULL"), nullable=True),
        _user_fk("created_by"),
        _now("created_at"),
        sa.CheckConstraint("kind IN ('daily', 'lesson')", name="ck_attendance_register_kind"),
    )
    op.create_index(
        "ix_attendance_registers_tenant_date", "attendance_registers", ["tenant_id", "date"]
    )
    op.create_index(
        "uq_attendance_register_daily",
        "attendance_registers",
        ["tenant_id", "class_name", "date"],
        unique=True,
        postgresql_where=sa.text("kind = 'daily'"),
    )
    op.create_index(
        "uq_attendance_register_lesson",
        "attendance_registers",
        ["lesson_id", "date"],
        unique=True,
        postgresql_where=sa.text("lesson_id IS NOT NULL"),
    )
    op.create_index(
        "uq_attendance_register_adhoc",
        "attendance_registers",
        [
            "tenant_id",
            "class_name",
            sa.text("coalesce(stream, '')"),
            "subject_id",
            "date",
            "starts_at",
        ],
        unique=True,
        postgresql_where=sa.text("kind = 'lesson' AND lesson_id IS NULL"),
    )

    op.create_table(
        "attendance_marks",
        sa.Column("id", sa.UUID(), primary_key=True),
        _tenant_fk(),
        sa.Column(
            "register_id",
            sa.UUID(),
            sa.ForeignKey("attendance_registers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "student_id", sa.UUID(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("class_name", sa.String(), nullable=False),
        sa.Column("stream", sa.String(), nullable=True),
        _user_fk("marked_by"),
        sa.Column("tapped_at", sa.DateTime(timezone=True), nullable=False),
        _now("updated_at"),
        sa.UniqueConstraint("register_id", "student_id", name="uq_attendance_mark_register_student"),
    )
    op.create_index(
        "ix_attendance_marks_tenant_student", "attendance_marks", ["tenant_id", "student_id"]
    )

    op.create_table(
        "attendance_correction_requests",
        sa.Column("id", sa.UUID(), primary_key=True),
        _tenant_fk(),
        sa.Column(
            "register_id",
            sa.UUID(),
            sa.ForeignKey("attendance_registers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _user_fk("requested_by"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        _user_fk("decided_by"),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        _now("created_at"),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')", name="ck_correction_request_status"
        ),
    )
    op.create_index(
        "ix_attendance_correction_requests_tenant_status",
        "attendance_correction_requests",
        ["tenant_id", "status"],
    )

    op.create_table(
        "attendance_correction_request_items",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "request_id",
            sa.UUID(),
            sa.ForeignKey("attendance_correction_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "student_id", sa.UUID(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("from_status", sa.String(), nullable=True),
        sa.Column("to_status", sa.String(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
    )

    op.create_table(
        "attendance_mark_changes",
        sa.Column("id", sa.UUID(), primary_key=True),
        _tenant_fk(),
        sa.Column(
            "mark_id",
            sa.UUID(),
            sa.ForeignKey("attendance_marks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(), nullable=True),
        sa.Column("to_status", sa.String(), nullable=False),
        sa.Column("from_note", sa.Text(), nullable=True),
        sa.Column("to_note", sa.Text(), nullable=True),
        _user_fk("changed_by"),
        _now("changed_at"),
        sa.Column("tapped_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "correction_request_id",
            sa.UUID(),
            sa.ForeignKey("attendance_correction_requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_attendance_mark_changes_mark_id", "attendance_mark_changes", ["mark_id"])

    op.create_table(
        "no_school_days",
        sa.Column("id", sa.UUID(), primary_key=True),
        _tenant_fk(),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        _user_fk("created_by"),
        _now("created_at"),
        sa.UniqueConstraint("tenant_id", "date", name="uq_no_school_day_tenant_date"),
    )

    op.create_table(
        "push_tokens",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("token", sa.String(), nullable=False, unique=True),
        _user_fk("user_id", nullable=False, ondelete="CASCADE"),
        _tenant_fk(),
        _now("created_at"),
        _now("updated_at"),
    )
    op.create_index("ix_push_tokens_user_id", "push_tokens", ["user_id"])

    _copy_old_records()


def _copy_old_records() -> None:
    """
    The old per-student records were whole-day attendance with no subject, so each
    becomes a mark on its class's daily register for that date. Where a student had
    several records on one date, the latest wins. Students without a class get "".
    The old tables stay (read-only) until the web tab has moved to registers.
    """
    op.execute(
        """
        CREATE TEMP TABLE old_marks ON COMMIT DROP AS
        SELECT DISTINCT ON (r.profile_id, r.date)
               s.tenant_id, s.id AS student_id, coalesce(s.class_applied, '') AS class_name,
               s.faculty AS stream, r.date, r.status, r.note, r.created_at
        FROM student_attendance_records r
        JOIN student_attendance_profiles p ON p.id = r.profile_id
        JOIN students s ON s.id = p.student_id AND s.tenant_id = r.tenant_id
        ORDER BY r.profile_id, r.date, r.created_at DESC, r.id DESC
        """
    )
    op.execute(
        """
        INSERT INTO attendance_registers (id, tenant_id, kind, date, class_name, created_at)
        SELECT gen_random_uuid(), tenant_id, 'daily', date, class_name, min(created_at)
        FROM old_marks
        GROUP BY tenant_id, class_name, date
        """
    )
    op.execute(
        """
        INSERT INTO attendance_marks
            (id, tenant_id, register_id, student_id, status, note, class_name, stream,
             tapped_at, updated_at)
        SELECT gen_random_uuid(), o.tenant_id, g.id, o.student_id, o.status, o.note,
               o.class_name, o.stream, o.created_at, o.created_at
        FROM old_marks o
        JOIN attendance_registers g
          ON g.kind = 'daily' AND g.tenant_id = o.tenant_id
         AND g.class_name = o.class_name AND g.date = o.date
        """
    )
    op.execute(
        """
        INSERT INTO attendance_mark_changes
            (id, tenant_id, mark_id, from_status, to_status, to_note, changed_at, tapped_at, reason)
        SELECT gen_random_uuid(), tenant_id, id, NULL, status, note, tapped_at, tapped_at,
               'Copied from the earlier attendance records'
        FROM attendance_marks
        """
    )


def downgrade() -> None:
    op.drop_table("push_tokens")
    op.drop_table("no_school_days")
    op.drop_table("attendance_mark_changes")
    op.drop_table("attendance_correction_request_items")
    op.drop_table("attendance_correction_requests")
    op.drop_table("attendance_marks")
    op.drop_table("attendance_registers")
    op.drop_table("student_subject_changes")
    op.drop_table("student_subjects")
    op.drop_table("academic_term_dates")
    op.drop_constraint("uq_staff_tenant_user", "staff", type_="unique")
    op.drop_column("staff", "user_id")
    op.drop_column("tenants", "timezone")
