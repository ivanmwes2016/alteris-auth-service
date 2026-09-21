import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException, status
from gotrue.errors import AuthApiError
from pydantic import ValidationError

from app.crud import team
from app.db.schemas.team import InviteCreate, RoleUpdate, TeamRole, normalize_email


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
# Invite email delivery
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _frontend_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        team, "get_settings", lambda: SimpleNamespace(FRONTEND_URL="https://app.test/")
    )


def _clients(
    invite_error: Exception | None = None, otp_error: Exception | None = None
) -> tuple[Mock, Mock]:
    admin, anon = Mock(), Mock()
    admin.auth.admin.invite_user_by_email = Mock(side_effect=invite_error)
    anon.auth.sign_in_with_otp = Mock(side_effect=otp_error)
    return admin, anon


ACCEPT_URL = "https://app.test/accept-invite?token=tok123"
SIGNUP_URL = "https://app.test/signup?invite=tok123"
DATA = {"first_name": "Ada", "workspace_name": "Vienna", "invited_role": "Teacher"}


@pytest.mark.asyncio
async def test_new_address_gets_the_supabase_invite_email_linking_to_signup() -> None:
    admin, anon = _clients()

    await team._send_invite_email(admin, anon, "a@b.com", "tok123", DATA)

    admin.auth.admin.invite_user_by_email.assert_called_once_with(
        "a@b.com", {"redirect_to": SIGNUP_URL, "data": DATA}
    )
    anon.auth.sign_in_with_otp.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "already_registered",
    [
        AuthApiError("exists", 422, "email_exists"),
        AuthApiError("A user with this email address has already been registered", 422, "x"),
    ],
)
async def test_existing_account_still_gets_an_email_via_a_one_time_sign_in_link(
    already_registered: AuthApiError,
) -> None:
    admin, anon = _clients(invite_error=already_registered)

    await team._send_invite_email(admin, anon, "a@b.com", "tok123", DATA)

    anon.auth.sign_in_with_otp.assert_called_once_with(
        {
            "email": "a@b.com",
            "options": {"email_redirect_to": ACCEPT_URL, "should_create_user": False},
        }
    )


@pytest.mark.asyncio
async def test_rate_limit_on_the_invite_email_maps_to_429_without_falling_back() -> None:
    admin, anon = _clients(invite_error=AuthApiError("slow", 429, "over_email_send_rate_limit"))

    with pytest.raises(HTTPException) as error:
        await team._send_invite_email(admin, anon, "a@b.com", "tok", DATA)

    assert error.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    anon.auth.sign_in_with_otp.assert_not_called()


@pytest.mark.asyncio
async def test_rate_limit_on_the_fallback_email_maps_to_429() -> None:
    admin, anon = _clients(
        invite_error=AuthApiError("exists", 422, "email_exists"),
        otp_error=AuthApiError("slow", 429, "over_email_send_rate_limit"),
    )

    with pytest.raises(HTTPException) as error:
        await team._send_invite_email(admin, anon, "a@b.com", "tok", DATA)

    assert error.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invite_error",
    [AuthApiError("smtp down", 500, "unexpected_failure"), RuntimeError("network")],
)
async def test_other_invite_failures_map_to_502_and_do_not_try_the_fallback(
    invite_error: Exception,
) -> None:
    admin, anon = _clients(invite_error=invite_error)

    with pytest.raises(HTTPException) as error:
        await team._send_invite_email(admin, anon, "a@b.com", "tok", DATA)

    assert error.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert "smtp" not in str(error.value.detail)
    anon.auth.sign_in_with_otp.assert_not_called()


@pytest.mark.asyncio
async def test_failure_of_the_fallback_email_maps_to_502() -> None:
    admin, anon = _clients(
        invite_error=AuthApiError("exists", 422, "email_exists"),
        otp_error=AuthApiError("smtp down", 500, "unexpected_failure"),
    )

    with pytest.raises(HTTPException) as error:
        await team._send_invite_email(admin, anon, "a@b.com", "tok", DATA)

    assert error.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert "smtp" not in str(error.value.detail)


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
# What the invite email template gets to work with
# ---------------------------------------------------------------------------
def _invite(first: str | None, last: str | None) -> SimpleNamespace:
    return SimpleNamespace(first_name=first, last_name=last)


def test_email_data_carries_name_workspace_and_role() -> None:
    data = team._invite_email_data(
        _invite("Ada", "Lovelace"), SimpleNamespace(name="bursar"), "Vienna"
    )  # type: ignore[arg-type]

    assert data == {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "workspace_name": "Vienna",
        "invited_role": "Bursar",
    }


def test_email_data_omits_missing_names_so_templates_can_branch_on_them() -> None:
    data = team._invite_email_data(_invite(None, None), SimpleNamespace(name="teacher"), "Vienna")  # type: ignore[arg-type]

    assert data == {"workspace_name": "Vienna", "invited_role": "Teacher"}
