"""
Link marks saved without a student_id to their student, the same way migration
c4e9a2d7f310 did: the student in that class with that exact name when exactly
one matches, otherwise the only student in the school with that name.

Needed once after deploying the student_id change: marks saved by the old API
between the migration and the deploy have no student_id. Safe to run again;
it only touches marks with no student_id.

    uv run python -m scripts.link_performance_marks           # dry run: counts only
    uv run python -m scripts.link_performance_marks --apply   # link them

Uses DATABASE_URL_SYNC (from .env when it isn't set), and prints which database
it's about to use before doing anything.
"""

import os
import sys
from urllib.parse import urlparse

import sqlalchemy as sa
from dotenv import load_dotenv

# Each pass finds one student per unlinked mark, then links it unless that
# student already has a mark for the same subject, class and term (saved by
# id meanwhile), which is left as is rather than overwritten.
_LINK = """
    UPDATE performance_records pr
    SET student_id = m.student_id
    FROM (
        SELECT pr2.id AS record_id, MIN(s.id::text)::uuid AS student_id
        FROM performance_records pr2
        {join}
        WHERE pr2.student_id IS NULL
        GROUP BY pr2.id
        HAVING COUNT(DISTINCT s.id) = 1
    ) m
    WHERE pr.id = m.record_id
      AND NOT EXISTS (
        SELECT 1 FROM performance_records taken
        WHERE taken.student_id = m.student_id
          AND taken.tenant_id = pr.tenant_id
          AND taken.class_name = pr.class_name
          AND taken.term_label = pr.term_label
          AND taken.year = pr.year
          AND taken.subject = pr.subject
      )
"""

# 1. The student in that class (name + stream) with that name.
BY_CLASS = _LINK.format(
    join="""
        JOIN school_classes c
          ON c.tenant_id = pr2.tenant_id
         AND concat_ws(' ', c.name, NULLIF(c.stream, '')) = pr2.class_name
        JOIN students s
          ON s.tenant_id = pr2.tenant_id
         AND s.name = pr2.student_name
         AND s.class_applied = c.name
         AND COALESCE(NULLIF(s.faculty, ''), '') = COALESCE(NULLIF(c.stream, ''), '')
    """
)

# 2. Otherwise the only student in the school with that name.
BY_SCHOOL = _LINK.format(
    join="JOIN students s ON s.tenant_id = pr2.tenant_id AND s.name = pr2.student_name"
)

UNLINKED = "SELECT COUNT(*) FROM performance_records WHERE student_id IS NULL"


def main() -> None:
    apply = "--apply" in sys.argv[1:]
    load_dotenv(".env")
    url = os.environ.get("DATABASE_URL_SYNC")
    if not url:
        sys.exit("DATABASE_URL_SYNC is not set")
    parts = urlparse(url)
    print(f"Database: {parts.hostname}:{parts.port}{parts.path}")

    engine = sa.create_engine(url)
    with engine.begin() as conn:
        before = conn.execute(sa.text(UNLINKED)).scalar() or 0
        print(f"Marks without a student: {before}")
        if not apply:
            print("Dry run; nothing changed. Run with --apply to link them.")
            return

        conn.execute(sa.text(BY_CLASS))
        conn.execute(sa.text(BY_SCHOOL))
        after = conn.execute(sa.text(UNLINKED)).scalar() or 0
        print(f"Linked {before - after}; still without a student: {after}")


if __name__ == "__main__":
    main()
