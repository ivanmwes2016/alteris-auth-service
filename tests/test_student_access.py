import pytest

from app.crud.student_access import StudentAccess, student_access
from app.crud.team import ROLE_DB_NAME, display_role
from app.db.schemas.team import TeamRole

EVERYTHING = StudentAccess(
    view_records=True,
    add_students=True,
    edit_details=True,
    edit_marks=True,
    edit_attendance=True,
    edit_behaviour=True,
    edit_medical=True,
)
PROFILE_ONLY = StudentAccess(
    view_records=False,
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


def test_teachers_change_marks_attendance_and_behaviour_only() -> None:
    assert student_access(is_owner=False, role_name="teacher") == StudentAccess(
        view_records=True,
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
