import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException, status
from pydantic import ValidationError

from app.crud import team
from app.db.schemas.team import (
    AcceptInvite,
    InviteCreate,
    RoleUpdate,
    TeamRole,
    normalize_email,
)
from app.services.email import EmailError, invite_email


# ---------------------------------------------------------------------------
# Role display + permissions
# ---------------------------------------------------------------------------
def test_owner_is_presented_as_owner_whatever_the_stored_role() -> None:
    assert team.display_role("admin", is_owner=True) == "Owner"
    assert team.display_role("admin", is_owner=False) == "Admin"


@pytest.mark.parametrize(
    ("db_name", "label"),
    [
        ("head_teacher", "Head Teacher"),
        ("admissions_officer", "Admissions Officer"),
        ("teacher", "Teacher"),
        ("bursar", "Bursar"),
    ],
)
def test_stored_role_names_map_to_ui_labels(db_name: str, label: str) -> None:
    assert team.display_role(db_name, is_owner=False) == label


def test_every_assignable_role_has_a_stored_name() -> None:
    assert set(team.ROLE_DB_NAME) == set(TeamRole)


def test_owner_is_not_an_assignable_role() -> None:
    assert "Owner" not in {role.value for role in TeamRole}


@pytest.mark.parametrize(
    ("is_owner", "role_name", "allowed"),
    [
        (True, "admin", True),
        (False, "head_teacher", True),
        (False, "admin", False),
        (False, "teacher", False),
        (False, "bursar", False),
        (False, "admissions_officer", False),
    ],
)
def test_only_owner_and_head_teacher_can_manage_users(
    is_owner: bool, role_name: str, allowed: bool
) -> None:
    assert team.can_manage_users(is_owner=is_owner, role_name=role_name) is allowed


def _row(tenant_id: uuid.UUID, role_name: str, owner_id: uuid.UUID | None) -> Mock:
    result = Mock()
    result.first.return_value = (tenant_id, role_name, owner_id)
    db = Mock()
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_manager_check_rejects_a_teacher() -> None:
    user = SimpleNamespace(id=uuid.uuid4())
    db = _row(uuid.uuid4(), "teacher", owner_id=uuid.uuid4())

    with pytest.raises(HTTPException) as error:
        await team.get_manager(db, user)  # type: ignore[arg-type]

    assert error.value.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_manager_check_accepts_owner_and_head_teacher() -> None:
    owner = SimpleNamespace(id=uuid.uuid4())
    as_owner = await team.get_manager(_row(uuid.uuid4(), "admin", owner.id), owner)  # type: ignore[arg-type]
    assert as_owner.is_owner

    head = SimpleNamespace(id=uuid.uuid4())
    as_head = await team.get_manager(_row(uuid.uuid4(), "head_teacher", uuid.uuid4()), head)  # type: ignore[arg-type]
    assert not as_head.is_owner


@pytest.mark.asyncio
async def test_actor_without_an_active_membership_is_rejected() -> None:
    result = Mock()
    result.first.return_value = None
    db = Mock()
    db.execute = AsyncMock(return_value=result)

    with pytest.raises(HTTPException) as error:
        await team.get_actor(db, SimpleNamespace(id=uuid.uuid4()))  # type: ignore[arg-type]

    assert error.value.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# Seats
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("seat_limit", "members", "pending", "available"),
    [
        (None, 50, 50, True),
        (5, 3, 0, True),
        (5, 5, 0, False),
        (5, 3, 2, False),
        (5, 3, 1, True),
        (2, 1, 1, False),
    ],
)
def test_seat_availability_counts_pending_invites(
    seat_limit: int | None, members: int, pending: int, available: bool
) -> None:
    assert (
        team.seats_available(seat_limit=seat_limit, members=members, pending_invites=pending)
        is available
    )


# ---------------------------------------------------------------------------
# Request contract
# ---------------------------------------------------------------------------
def test_invite_email_is_normalised() -> None:
    invite = InviteCreate(email="  Grace@School.ORG ", role=TeamRole.teacher)
    assert invite.email == "grace@school.org"


@pytest.mark.parametrize(
    "bad", ["", "no-at-sign", "a@b", "a@@b.com", "a b@c.com", "@b.com", "a@.com", "a@b."]
)
def test_invalid_emails_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="valid email"):
        normalize_email(bad)


def test_owner_role_cannot_be_invited_or_assigned() -> None:
    with pytest.raises(ValidationError):
        InviteCreate(email="a@b.com", role="Owner")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        RoleUpdate(role="Owner")  # type: ignore[arg-type]


def test_unknown_request_fields_fail_loudly_instead_of_being_dropped() -> None:
    with pytest.raises(ValidationError) as error:
        InviteCreate.model_validate({"email": "a@b.com", "role": "Teacher", "name": "Grace"})

    assert "name" in str(error.value)


# ---------------------------------------------------------------------------
# Invite email delivery (Resend)
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _frontend_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        team, "get_settings", lambda: SimpleNamespace(FRONTEND_URL="https://app.test/")
    )


def _pending(first: str | None = "Ada") -> SimpleNamespace:
    return SimpleNamespace(email="a@b.com", token="tok123", first_name=first)


def test_invite_link_opens_the_invite_page_and_signs_no_one_in() -> None:
    assert team.build_invite_url("tok123") == "https://app.test/accept-invite?token=tok123"


@pytest.mark.asyncio
async def test_invite_email_goes_to_the_invitee_with_the_invite_link() -> None:
    send = AsyncMock()
    with patch.object(team, "send_email", send):
        await team._send_invite_email(_pending(), SimpleNamespace(name="teacher"), "Vienna")  # type: ignore[arg-type]

    kwargs = send.await_args.kwargs
    assert kwargs["to"] == "a@b.com"
    assert "Vienna" in kwargs["subject"]
    assert "https://app.test/accept-invite?token=tok123" in kwargs["html_body"]
    assert "https://app.test/accept-invite?token=tok123" in kwargs["text_body"]
    assert "Teacher" in kwargs["text_body"]
    assert "Hi Ada," in kwargs["text_body"]


@pytest.mark.asyncio
async def test_resend_rate_limit_maps_to_429() -> None:
    with (
        patch.object(team, "send_email", AsyncMock(side_effect=EmailError("x", rate_limited=True))),
        pytest.raises(HTTPException) as error,
    ):
        await team._send_invite_email(_pending(), SimpleNamespace(name="teacher"), "V")  # type: ignore[arg-type]

    assert error.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.asyncio
async def test_other_email_failures_map_to_502_without_leaking_details() -> None:
    failure = EmailError("Resend rejected the email: 403 domain not verified")
    with (
        patch.object(team, "send_email", AsyncMock(side_effect=failure)),
        pytest.raises(HTTPException) as error,
    ):
        await team._send_invite_email(_pending(), SimpleNamespace(name="teacher"), "V")  # type: ignore[arg-type]

    assert error.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert "domain" not in str(error.value.detail)


# ---------------------------------------------------------------------------
# Invitee name (the invite dialog sends first_name / last_name)
# ---------------------------------------------------------------------------
def test_invite_names_are_optional() -> None:
    invite = InviteCreate(email="a@b.com", role=TeamRole.teacher)
    assert invite.first_name is None
    assert invite.last_name is None


def test_invite_names_are_trimmed_and_blank_becomes_none() -> None:
    invite = InviteCreate(
        email="a@b.com", role=TeamRole.teacher, first_name="  Ada   Mary ", last_name="   "
    )
    assert invite.first_name == "Ada Mary"
    assert invite.last_name is None


def test_invite_names_have_a_length_limit() -> None:
    with pytest.raises(ValidationError):
        InviteCreate(email="a@b.com", role=TeamRole.teacher, first_name="x" * 101)


@pytest.mark.parametrize(
    ("first", "last", "expected"),
    [
        ("Ada", "Lovelace", "Ada Lovelace"),
        ("Ada", None, "Ada"),
        (None, "Lovelace", "Lovelace"),
        (None, None, None),
    ],
)
def test_pending_invite_shows_the_typed_name(
    first: str | None, last: str | None, expected: str | None
) -> None:
    invite = SimpleNamespace(id=uuid.uuid4(), email="a@b.com", first_name=first, last_name=last)
    row = team._invite_read(invite, SimpleNamespace(name="teacher"))  # type: ignore[arg-type]

    assert row.name == expected
    assert row.status == "Invited"


# ---------------------------------------------------------------------------
# Invite email content
# ---------------------------------------------------------------------------
def test_email_escapes_school_names_in_html() -> None:
    _, html_body, text_body = invite_email(
        invite_url="https://app.test/accept-invite?token=t",
        workspace_name="<b>St Mary's</b>",
        role_label="Teacher",
        first_name=None,
    )
    assert "<b>St Mary" not in html_body
    assert "&lt;b&gt;" in html_body
    assert text_body.startswith("Hi,")


# ---------------------------------------------------------------------------
# Removing members
# ---------------------------------------------------------------------------
def _removal() -> tuple[Mock, Mock, team.Actor, uuid.UUID]:
    tenant_id, owner_id = uuid.uuid4(), uuid.uuid4()
    member = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4())
    result = Mock()
    result.first.return_value = (member, SimpleNamespace(), SimpleNamespace(), None)
    db = Mock()
    db.scalar = AsyncMock(return_value=owner_id)
    db.execute = AsyncMock(return_value=result)
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    supabase = Mock()
    actor = team.Actor(user_id=owner_id, tenant_id=tenant_id, role_name="admin", is_owner=True)
    return db, supabase, actor, member.id


@pytest.mark.asyncio
async def test_removal_ends_only_the_membership_and_bans_no_one() -> None:
    db, supabase, actor, target_id = _removal()

    with patch.object(team, "remove_if_stale", AsyncMock()) as cleanup:
        await team.remove_member(db, supabase, actor, target_id)

    db.delete.assert_awaited_once()
    db.commit.assert_awaited_once()
    supabase.auth.admin.update_user_by_id.assert_not_called()
    cleanup.assert_awaited_once()


# ---------------------------------------------------------------------------
# Accepting an invite
# ---------------------------------------------------------------------------
def _accept_db(invite: SimpleNamespace | None, *, member_here: bool = False) -> Mock:
    db = Mock()
    invite_result = Mock()
    invite_result.scalar_one_or_none.return_value = invite
    role_result = Mock()
    role_result.scalar_one.return_value = SimpleNamespace(id=uuid.uuid4(), name="teacher")
    db.execute = AsyncMock(side_effect=[invite_result, role_result])
    db.scalar = AsyncMock(return_value=uuid.uuid4() if member_here else None)
    db.commit = AsyncMock()
    return db


def _invite_row(**overrides: object) -> SimpleNamespace:
    values = {
        "email": "a@b.com",
        "tenant_id": uuid.uuid4(),
        "role_id": uuid.uuid4(),
        "expires_at": datetime.now(UTC) + timedelta(days=1),
        "accepted_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


USER = SimpleNamespace(id=uuid.uuid4())


@pytest.mark.asyncio
async def test_unverified_email_cannot_accept() -> None:
    with pytest.raises(HTTPException) as error:
        await team.accept_invite(
            _accept_db(_invite_row()),
            USER,
            "tok",
            email="a@b.com",
            email_verified=False,  # type: ignore[arg-type]
        )

    assert error.value.status_code == status.HTTP_403_FORBIDDEN
    assert error.value.detail == "Email verification required."


@pytest.mark.asyncio
async def test_a_different_signed_in_email_cannot_accept() -> None:
    with pytest.raises(HTTPException) as error:
        await team.accept_invite(
            _accept_db(_invite_row()),
            USER,
            "tok",
            email="eve@b.com",
            email_verified=True,  # type: ignore[arg-type]
        )

    assert error.value.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_email_match_ignores_case_and_spaces() -> None:
    db = _accept_db(_invite_row(), member_here=True)

    await team.accept_invite(db, USER, "tok", email=" A@B.com ", email_verified=True)  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invite",
    [None, _invite_row(expires_at=datetime.now(UTC) - timedelta(seconds=1))],
    ids=["unknown", "expired"],
)
async def test_unknown_or_expired_invites_get_one_generic_404(
    invite: SimpleNamespace | None,
) -> None:
    with pytest.raises(HTTPException) as error:
        await team.accept_invite(
            _accept_db(invite),
            USER,
            "tok",
            email="a@b.com",
            email_verified=True,  # type: ignore[arg-type]
        )

    assert error.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_accepting_again_after_joining_is_not_an_error() -> None:
    invite = _invite_row(accepted_at=datetime.now(UTC))
    db = _accept_db(invite, member_here=True)

    response = await team.accept_invite(db, USER, "tok", email="a@b.com", email_verified=True)  # type: ignore[arg-type]

    assert response.tenantId == invite.tenant_id


@pytest.mark.asyncio
async def test_a_used_invite_cannot_be_reused_by_a_non_member() -> None:
    db = _accept_db(_invite_row(accepted_at=datetime.now(UTC)), member_here=False)

    with pytest.raises(HTTPException) as error:
        await team.accept_invite(db, USER, "tok", email="a@b.com", email_verified=True)  # type: ignore[arg-type]

    assert error.value.status_code == status.HTTP_404_NOT_FOUND


def test_accept_takes_optional_names_from_the_invite_page() -> None:
    payload = AcceptInvite.model_validate(
        {"token": "t", "first_name": "  Ivan ", "last_name": "Mwesigwa"}
    )
    assert (payload.first_name, payload.last_name) == ("Ivan", "Mwesigwa")
    assert AcceptInvite.model_validate({"token": "t"}).first_name is None


def test_accept_no_longer_takes_a_password() -> None:
    with pytest.raises(ValidationError):
        AcceptInvite.model_validate({"token": "t", "password": "hunter22"})
