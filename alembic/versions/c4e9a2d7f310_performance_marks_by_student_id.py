"""link performance marks to students by id

Marks were stored against the student's name, so renaming a student hid their
marks and two students with the same name in a class shared one row. This adds
performance_records.student_id (students.id) and fills it in for existing marks:

1. the student in that class (name + stream, as the Performance roster matches
   them) with that exact name, when exactly one matches;
2. otherwise the only student in the school with that name (they may have
   changed class since).

Marks still unmatched keep student_id NULL and stay keyed by name, so nothing is
lost; the upgrade prints how many there are.

Revision ID: c4e9a2d7f310
Revises: b3f8d61e2a47
Create Date: 2026-10-23 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e9a2d7f310"
down_revision: str | None = "b3f8d61e2a47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("performance_records", sa.Column("student_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_performance_records_student_id",
        "performance_records",
        "students",
        ["student_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 1. The student in that class with that name, when there's exactly one.
    op.execute(
        """
        UPDATE performance_records pr
        SET student_id = m.student_id
        FROM (
            SELECT pr2.id AS record_id, MIN(s.id::text)::uuid AS student_id
            FROM performance_records pr2
            JOIN school_classes c
              ON c.tenant_id = pr2.tenant_id
             AND concat_ws(' ', c.name, NULLIF(c.stream, '')) = pr2.class_name
            JOIN students s
              ON s.tenant_id = pr2.tenant_id
             AND s.name = pr2.student_name
             AND s.class_applied = c.name
             AND COALESCE(NULLIF(s.faculty, ''), '') = COALESCE(NULLIF(c.stream, ''), '')
            GROUP BY pr2.id
            HAVING COUNT(DISTINCT s.id) = 1
        ) m
        WHERE pr.id = m.record_id
        """
    )

    # 2. Otherwise the only student in the school with that name.
    op.execute(
        """
        UPDATE performance_records pr
        SET student_id = m.student_id
        FROM (
            SELECT pr2.id AS record_id, MIN(s.id::text)::uuid AS student_id
            FROM performance_records pr2
            JOIN students s
              ON s.tenant_id = pr2.tenant_id AND s.name = pr2.student_name
            WHERE pr2.student_id IS NULL
            GROUP BY pr2.id
            HAVING COUNT(DISTINCT s.id) = 1
        ) m
        WHERE pr.id = m.record_id
        """
    )

    # Two old rows (e.g. two spellings of one name) can now point at the same
    # student and subject; keep the most recently updated one.
    op.execute(
        """
        DELETE FROM performance_records pr
        USING performance_records newer
        WHERE pr.student_id IS NOT NULL
          AND newer.student_id = pr.student_id
          AND newer.tenant_id = pr.tenant_id
          AND newer.class_name = pr.class_name
          AND newer.term_label = pr.term_label
          AND newer.year = pr.year
          AND newer.subject = pr.subject
          AND (newer.updated_at, newer.id::text) > (pr.updated_at, pr.id::text)
        """
    )

    unmatched = (
        op.get_bind()
        .execute(sa.text("SELECT COUNT(*) FROM performance_records WHERE student_id IS NULL"))
        .scalar()
    )
    print(
        f"performance_records: {unmatched} mark(s) couldn't be matched to a student; kept by name"
    )

    op.drop_constraint("uq_performance_record", "performance_records", type_="unique")
    op.create_unique_constraint(
        "uq_performance_record_student",
        "performance_records",
        ["tenant_id", "class_name", "term_label", "year", "student_id", "subject"],
    )
    op.create_index(
        "uq_performance_record_unmatched_name",
        "performance_records",
        ["tenant_id", "class_name", "term_label", "year", "student_name", "subject"],
        unique=True,
        postgresql_where=sa.text("student_id IS NULL"),
    )
    op.create_index(
        "ix_performance_records_student_id", "performance_records", ["student_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_performance_records_student_id", table_name="performance_records")
    op.drop_index("uq_performance_record_unmatched_name", table_name="performance_records")
    op.drop_constraint("uq_performance_record_student", "performance_records", type_="unique")
    # Going back to one row per name: keep the newest where names now collide.
    op.execute(
        """
        DELETE FROM performance_records pr
        USING performance_records newer
        WHERE newer.tenant_id = pr.tenant_id
          AND newer.class_name = pr.class_name
          AND newer.term_label = pr.term_label
          AND newer.year = pr.year
          AND newer.student_name = pr.student_name
          AND newer.subject = pr.subject
          AND (newer.updated_at, newer.id::text) > (pr.updated_at, pr.id::text)
        """
    )
    op.create_unique_constraint(
        "uq_performance_record",
        "performance_records",
        ["tenant_id", "class_name", "term_label", "year", "student_name", "subject"],
    )
    op.drop_constraint(
        "fk_performance_records_student_id", "performance_records", type_="foreignkey"
    )
    op.drop_column("performance_records", "student_id")
