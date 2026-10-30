import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException, status

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
