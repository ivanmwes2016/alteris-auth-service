from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api.v1.routes import auth
from app.db.models.tenant import Tenant
from app.helpers.phone import clean_phone


# ── The phone rule ───────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("typed", "kept"),
    [
        ("+256 700 123456", "+256 700 123456"),
        ("0700123456", "0700123456"),
        ("  +44  (0)20 7946-0958 ", "+44 (0)20 7946-0958"),
        ("020.7946.0958", "020.7946.0958"),
    ],
)
def test_real_numbers_are_kept_with_their_spacing_tidied(typed: str, kept: str) -> None:
    assert clean_phone(typed) == kept


@pytest.mark.parametrize(
    "typed",
    [None, "", "   ", "asdf", "12345", "0700123456789012", "+256 700 12345x", "256+700", 700],
)
def test_junk_and_impossible_lengths_are_refused(typed: object) -> None:
    assert clean_phone(typed) is None


# ── Registering a school ─────────────────────────────────────────────────────
def _db() -> Mock:
    db = Mock()
    db.scalar = AsyncMock(return_value=None)  # not yet in a workspace
    role = Mock()
    role.scalar_one.return_value = SimpleNamespace(id="admin-role")
    db.execute = AsyncMock(return_value=role)
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


async def _signup(db: Mock, tasks: BackgroundTasks, **fields: Any) -> dict[str, str]:
    payload = {"name": "Glory School", "slug": "glory-school", "workspace_id": "org-1"}
    return await auth.signup(
        payload | fields,
        tasks,
        db=db,
        current_user=SimpleNamespace(id="user-1", email="owner@x.io", name="Ann Owner"),
        supabase_user=SimpleNamespace(user_metadata={}),
    )


@pytest.mark.parametrize("phone", [None, "", "not a number", "123"])
async def test_a_school_needs_a_real_phone_number(phone: str | None) -> None:
    db, tasks = _db(), BackgroundTasks()

    with pytest.raises(HTTPException) as error:
        await _signup(db, tasks, phone=phone)

    assert error.value.status_code == 400
    assert error.value.detail == auth.INVALID_PHONE
    db.add.assert_not_called()
    assert tasks.tasks == []


async def test_phone_type_and_source_are_saved_and_sent_to_the_platform_team() -> None:
    db, tasks = _db(), BackgroundTasks()

    await _signup(
        db,
        tasks,
        phone=" +256 700  123456",
        type="Nursery",
        source="  Friend or colleague ",
        country="Uganda",
    )

    tenant = db.add.call_args_list[0].args[0]
    assert isinstance(tenant, Tenant)
    assert tenant.phone == "+256 700 123456"
    assert tenant.school_type == "Nursery"
    assert tenant.referral_source == "Friend or colleague"
    assert tenant.status == "pending"

    alert = next(t for t in tasks.tasks if t.func is auth.notify_school_registered)
    assert alert.kwargs["phone"] == "+256 700 123456"
    assert alert.kwargs["school_type"] == "Nursery"
    assert alert.kwargs["referral_source"] == "Friend or colleague"


async def test_type_and_source_stay_optional() -> None:
    db = _db()
    await _signup(db, BackgroundTasks(), phone="0700123456", type="", source=["x"])

    tenant = db.add.call_args_list[0].args[0]
    assert tenant.school_type is None and tenant.referral_source is None
