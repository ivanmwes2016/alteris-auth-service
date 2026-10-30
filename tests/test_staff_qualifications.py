import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException, status

from app.crud import staff_access, team
from app.crud import staff_qualification_profile as qualifications
from app.db.schemas.staff_qualification_profile import QualificationProfileBulkUpdate


def db_without_staff() -> Mock:
    db = Mock()
    db.scalar = AsyncMock(return_value=None)
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_reading_another_schools_staff_member_404s_without_creating_a_profile() -> None:
    db = db_without_staff()
    with pytest.raises(HTTPException) as error:
        await qualifications.get_or_create_profile(
            db, staff_id=uuid.uuid4(), tenant_id=uuid.uuid4()
        )
    assert error.value.status_code == status.HTTP_404_NOT_FOUND
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_saving_another_schools_staff_member_404s_without_writing() -> None:
    db = db_without_staff()
    with pytest.raises(HTTPException) as error:
        await qualifications.sync_profile(
            db,
            staff_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            data=QualificationProfileBulkUpdate(notes="x"),
        )
    assert error.value.status_code == status.HTTP_404_NOT_FOUND
    db.execute.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    ("is_owner", "role", "allowed"),
    [
        (True, "teacher", True),
        (False, "admin", True),
        (False, "head_teacher", True),
        (False, "teacher", False),
        (False, "nurse", False),
        (False, "bursar", False),
        (False, "admissions_officer", False),
    ],
)
def test_only_owner_admin_and_head_teacher_edit_staff(
    is_owner: bool, role: str, allowed: bool
) -> None:
    assert staff_access.can_edit_staff(is_owner=is_owner, role_name=role) is allowed


@pytest.mark.asyncio
async def test_a_teacher_is_refused_staff_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    actor = team.Actor(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), role_name="teacher", is_owner=False
    )
    monkeypatch.setattr(team, "get_actor", AsyncMock(return_value=actor))
    with pytest.raises(HTTPException) as error:
        await staff_access.require_staff_editor(Mock(), Mock())
    assert error.value.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_a_head_teacher_gets_their_school(monkeypatch: pytest.MonkeyPatch) -> None:
    actor = team.Actor(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), role_name="head_teacher", is_owner=False
    )
    monkeypatch.setattr(team, "get_actor", AsyncMock(return_value=actor))
    assert await staff_access.require_staff_editor(Mock(), Mock()) == actor.tenant_id


@pytest.mark.parametrize(
    ("is_owner", "role", "allowed"),
    [
        (True, "teacher", True),
        (False, "admin", True),
        (False, "head_teacher", True),
        (False, "nurse", True),
        (False, "teacher", False),
        (False, "bursar", False),
    ],
)
def test_the_nurse_also_looks_after_staff_health(is_owner: bool, role: str, allowed: bool) -> None:
    assert staff_access.can_manage_staff_health(is_owner=is_owner, role_name=role) is allowed


@pytest.mark.asyncio
async def test_a_nurse_gets_health_but_not_other_staff_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = team.Actor(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), role_name="nurse", is_owner=False
    )
    monkeypatch.setattr(team, "get_actor", AsyncMock(return_value=actor))
    assert await staff_access.require_staff_health(Mock(), Mock()) == actor.tenant_id
    with pytest.raises(HTTPException) as error:
        await staff_access.require_staff_editor(Mock(), Mock())
    assert error.value.status_code == status.HTTP_403_FORBIDDEN
