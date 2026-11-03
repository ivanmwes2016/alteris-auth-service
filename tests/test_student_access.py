from types import SimpleNamespace
from typing import Any

import pytest

from app.crud.student_access import StudentAccess, may_read_medical, student_access
from app.crud.team import ROLE_DB_NAME, display_role
from app.db.schemas.team import TeamRole

EVERYTHING = StudentAccess(
    view_records=True,
    view_medical=True,
    add_students=True,
    edit_details=True,
    edit_marks=True,
    edit_attendance=True,
    edit_behaviour=True,
    edit_medical=True,
)
PROFILE_ONLY = StudentAccess(
    view_records=False,
    view_medical=False,
    add_students=False,
    edit_details=False,
    edit_marks=False,
    edit_attendance=False,
    edit_behaviour=False,
    edit_medical=False,
)


@pytest.mark.parametrize("role_name", ["admin", "head_teacher"])
def test_admins_and_head_teachers_do_everything(role_name: str) -> None:
    assert student_access(is_owner=False, role_name=role_name) == EVERYTHING


def test_the_owner_does_everything_whatever_their_role() -> None:
    assert student_access(is_owner=True, role_name="bursar") == EVERYTHING


def test_teachers_change_marks_attendance_and_behaviour_but_dont_read_all_medical() -> None:
    assert student_access(is_owner=False, role_name="teacher") == StudentAccess(
        view_records=True,
        view_medical=False,
        add_students=False,
        edit_details=False,
        edit_marks=True,
        edit_attendance=True,
        edit_behaviour=True,
        edit_medical=False,
    )


def test_nurses_change_medical_only() -> None:
    assert student_access(is_owner=False, role_name="nurse") == StudentAccess(
        view_records=True,
        view_medical=True,
        add_students=False,
        edit_details=False,
        edit_marks=False,
        edit_attendance=False,
        edit_behaviour=False,
        edit_medical=True,
    )


def test_bursars_see_the_profile_only() -> None:
    assert student_access(is_owner=False, role_name="bursar") == PROFILE_ONLY


def test_admissions_officers_see_the_profile_and_add_accepted_applicants() -> None:
    access = student_access(is_owner=False, role_name="admissions_officer")
    assert access.add_students
    assert access.view_records is False
    assert access.edit_details is False


def test_an_unknown_role_gets_nothing() -> None:
    assert student_access(is_owner=False, role_name="librarian") == PROFILE_ONLY


def test_nurse_is_an_assignable_team_role() -> None:
    assert ROLE_DB_NAME[TeamRole("Nurse")] == "nurse"
    assert display_role("nurse", is_owner=False) == "Nurse"


def test_nurses_read_attendance_but_dont_take_registers() -> None:
    from app.crud import attendance

    assert not attendance.can_take_registers(is_owner=False, role_name="nurse")
    assert student_access(is_owner=False, role_name="nurse").view_records


def _student(class_applied: str | None, faculty: str | None) -> Any:
    return SimpleNamespace(class_applied=class_applied, faculty=faculty)


TEACHER = student_access(is_owner=False, role_name="teacher")


@pytest.mark.parametrize("role_name", ["admin", "head_teacher", "nurse"])
def test_nurses_heads_and_admins_read_every_medical_record(role_name: str) -> None:
    access = student_access(is_owner=False, role_name=role_name)
    assert may_read_medical(access, own_class=None, student=_student("S.2", "West"))


def test_the_owner_reads_every_medical_record() -> None:
    access = student_access(is_owner=True, role_name="bursar")
    assert may_read_medical(access, own_class=None, student=_student("S.2", "West"))


def test_a_class_teacher_reads_their_own_class_medical_records() -> None:
    assert may_read_medical(TEACHER, own_class="S.1 East", student=_student("S.1", "East"))
    assert may_read_medical(TEACHER, own_class="s1 east", student=_student("S.1", "East"))


def test_a_class_teacher_doesnt_read_another_class() -> None:
    assert not may_read_medical(TEACHER, own_class="S.1 East", student=_student("S.1", "West"))
    assert not may_read_medical(TEACHER, own_class="S.1 East", student=_student(None, None))


def test_a_teacher_who_isnt_a_class_teacher_reads_no_medical_records() -> None:
    assert not may_read_medical(TEACHER, own_class=None, student=_student("S.1", "East"))


def test_a_bursar_named_on_a_class_still_reads_no_medical_records() -> None:
    bursar = student_access(is_owner=False, role_name="bursar")
    assert not may_read_medical(bursar, own_class="S.1 East", student=_student("S.1", "East"))
