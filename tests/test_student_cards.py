import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.crud import student_cards
from app.db.schemas.attendance import MarkIn, RegisterStudent
from app.db.schemas.student_card import CardLink, CardRead

TENANT = uuid.uuid4()
STUDENT = uuid.uuid4()
OTHER = uuid.uuid4()
USER: Any = SimpleNamespace(id=uuid.uuid4())


@pytest.mark.parametrize(
    "sent",
    ["04A2B3C4D5E680", "04:a2:b3:c4:d5:e6:80", "04-a2-b3-c4-d5-e6-80", " 04 a2 b3 c4 d5 e6 80 "],
)
def test_every_way_a_phone_writes_a_serial_stores_the_same(sent: str) -> None:
    assert CardLink(uid=sent).uid == "04A2B3C4D5E680"


@pytest.mark.parametrize("sent", ["", "04A2", "04A2B3C4D5E6", "ZZA2B3C4D5E680"])
def test_anything_else_is_refused(sent: str) -> None:
    with pytest.raises(ValidationError):
        CardLink(uid=sent)


def test_card_read_is_camel_case() -> None:
    read = CardRead(uid="04A2B3C4", linked_at=datetime(2026, 11, 9, tzinfo=UTC))
    assert set(read.model_dump(by_alias=True)) == {"uid", "linkedAt"}


def test_marks_count_as_manual_unless_the_phone_says_card() -> None:
    assert MarkIn(student_id=STUDENT, status="Present").source == "manual"
    assert MarkIn(student_id=STUDENT, status="Present", source="card").source == "card"
    with pytest.raises(ValidationError):
        MarkIn(student_id=STUDENT, status="Present", source="face")  # type: ignore[arg-type]


def test_register_students_carry_the_card_as_camel_case() -> None:
    row = RegisterStudent(student_id=STUDENT, name="Amara", card_uid="04A2B3C4")
    assert row.model_dump(by_alias=True)["cardUid"] == "04A2B3C4"


def _db(*scalars: Any) -> Any:
    """A session whose db.scalar calls answer in order."""
    return SimpleNamespace(
        scalar=AsyncMock(side_effect=list(scalars)),
        add=lambda row: added.append(row),
        commit=AsyncMock(),
        rollback=AsyncMock(),
        refresh=AsyncMock(),
        delete=AsyncMock(),
    )


added: list[Any] = []
_STUDENT_ROW = SimpleNamespace(id=STUDENT)


@pytest.mark.asyncio
async def test_a_card_on_someone_else_is_refused_by_name() -> None:
    db = _db(_STUDENT_ROW, None, OTHER, "Brian Okello")
    with pytest.raises(HTTPException) as caught:
        await student_cards.link_card(
            db, tenant_id=TENANT, student_id=STUDENT, uid="04A2B3C4", user=USER
        )
    assert caught.value.status_code == 409
    assert "Brian Okello" in caught.value.detail
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_a_new_card_replaces_the_old_one() -> None:
    existing = SimpleNamespace(uid="AAAAAAAA", linked_by=None)
    db = _db(_STUDENT_ROW, existing, None)
    card = await student_cards.link_card(
        db, tenant_id=TENANT, student_id=STUDENT, uid="04A2B3C4", user=USER
    )
    assert card is existing
    assert (card.uid, card.linked_by) == ("04A2B3C4", USER.id)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_relinking_their_own_card_is_fine() -> None:
    existing = SimpleNamespace(uid="04A2B3C4", linked_by=None)
    db = _db(_STUDENT_ROW, existing, STUDENT)
    await student_cards.link_card(
        db, tenant_id=TENANT, student_id=STUDENT, uid="04A2B3C4", user=USER
    )
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_student_from_another_school_is_not_found() -> None:
    db = _db(None)
    with pytest.raises(HTTPException) as caught:
        await student_cards.link_card(
            db, tenant_id=TENANT, student_id=STUDENT, uid="04A2B3C4", user=USER
        )
    assert caught.value.status_code == 404
