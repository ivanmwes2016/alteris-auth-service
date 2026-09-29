import uuid
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from gotrue.errors import AuthApiError
from sqlalchemy.exc import IntegrityError

from app.helpers.stale_users import remove_if_stale


def _db(attached: bool, delete_error: Exception | None = None) -> Mock:
    db = Mock()
    db.scalar = AsyncMock(return_value=attached)
    db.execute = AsyncMock(side_effect=delete_error)
    nested = MagicMock()
    nested.__aenter__ = AsyncMock()
    nested.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested = Mock(return_value=nested)
    return db


def _supabase(lookup_error: Exception | None) -> Mock:
    supabase = Mock()
    supabase.auth.admin.get_user_by_id = Mock(side_effect=lookup_error)
    return supabase


GONE = AuthApiError("User not found", 404, "user_not_found")


@pytest.mark.asyncio
async def test_leftover_with_nothing_attached_is_removed() -> None:
    db = _db(attached=False)

    assert await remove_if_stale(db, _supabase(GONE), uuid.uuid4())
    assert db.execute.await_count == 2  # profile + user


@pytest.mark.asyncio
async def test_row_with_memberships_or_a_school_is_kept() -> None:
    db = _db(attached=True)
    supabase = _supabase(GONE)

    assert not await remove_if_stale(db, supabase, uuid.uuid4())
    db.execute.assert_not_awaited()
    supabase.auth.admin.get_user_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_row_whose_login_still_exists_is_kept() -> None:
    db = _db(attached=False)

    assert not await remove_if_stale(db, _supabase(None), uuid.uuid4())
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lookup_error", [AuthApiError("down", 500, "unexpected_failure"), RuntimeError("network")]
)
async def test_row_is_kept_when_supabase_cannot_confirm(lookup_error: Exception) -> None:
    db = _db(attached=False)

    assert not await remove_if_stale(db, _supabase(lookup_error), uuid.uuid4())
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_row_referenced_by_an_unexpected_table_is_kept() -> None:
    db = _db(attached=False, delete_error=IntegrityError("delete", {}, Exception("fk")))

    assert not await remove_if_stale(db, _supabase(GONE), uuid.uuid4())
