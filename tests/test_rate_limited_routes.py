"""Rate limits as callers see them: through the real routes and services."""

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException, status
from tests.conftest import FakeUpstash

from app.api.v1.routes import auth, users
from app.db.models.auth import EmailRequest, RegisterRequest
from app.services import account

PASSWORD = "correct horse"


class FakeAuthService:
    """Supabase sign-in: only owner@school.org with PASSWORD exists."""

    def __init__(self) -> None:
        self.calls = 0

    async def login(self, email: str, password: str) -> Any:
        self.calls += 1
        if email == "owner@school.org" and password == PASSWORD:
            return {"access_token": "a", "refresh_token": "r", "token_type": "bearer"}
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")


@pytest.fixture
def service() -> FakeAuthService:
    return FakeAuthService()


@pytest.fixture
async def client(
    upstash: FakeUpstash, service: FakeAuthService
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(auth.router, prefix="/auth")
    app.dependency_overrides[auth.get_auth_service] = lambda: service
    transport = httpx.ASGITransport(app=app, client=("10.0.0.1", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as http:
        yield http


async def _login(
    http: httpx.AsyncClient, email: str, password: str = "wrong", ip: str = "198.51.100.1", **h: str
) -> httpx.Response:
    return await http.post(
        "/auth/login",
        json={"email": email, "password": password},
        headers={"cf-connecting-ip": ip, **h},
    )


# ── Login ────────────────────────────────────────────────────────────────────
async def test_failed_logins_lock_the_account_whichever_ips_they_come_from(
    client: httpx.AsyncClient, service: FakeAuthService
) -> None:
    for n in range(5):
        response = await _login(client, "owner@school.org", ip=f"198.51.100.{n}")
        assert response.status_code == 401

    # Even the right password is refused now, and Supabase isn't asked.
    response = await _login(client, "owner@school.org", PASSWORD, ip="203.0.113.99")

    assert response.status_code == 429
    assert response.headers["retry-after"] == "900"
    assert response.json() == {"detail": "Too many attempts. Please try again in 15 minutes."}
    assert service.calls == 5


async def test_a_successful_login_clears_the_accounts_failures(client: httpx.AsyncClient) -> None:
    for _ in range(4):
        await _login(client, "owner@school.org")
    assert (await _login(client, "owner@school.org", PASSWORD)).status_code == 200

    statuses = [(await _login(client, "owner@school.org")).status_code for _ in range(6)]
    assert statuses == [401] * 5 + [429]


async def test_unknown_emails_are_limited_exactly_like_real_ones(client: httpx.AsyncClient) -> None:
    real = [(await _login(client, "owner@school.org", ip="1.1.1.1")).status_code for _ in range(7)]
    fake = [(await _login(client, "nobody@school.org", ip="2.2.2.2")).status_code for _ in range(7)]
    assert real == fake == [401] * 5 + [429] * 2


async def test_the_ip_limit_stops_guessing_across_many_accounts(client: httpx.AsyncClient) -> None:
    for n in range(20):
        assert (await _login(client, f"user{n}@school.org")).status_code == 401

    assert (await _login(client, "user99@school.org")).status_code == 429
    assert (await _login(client, "user99@school.org", ip="198.51.100.2")).status_code == 401


async def test_spoofed_forwarded_for_headers_do_not_reset_the_ip_limit(
    client: httpx.AsyncClient,
) -> None:
    for n in range(20):
        await _login(client, f"user{n}@school.org", **{"x-forwarded-for": f"9.9.9.{n}"})

    response = await _login(client, "x@school.org", **{"x-forwarded-for": "9.9.9.250"})
    assert response.status_code == 429


async def test_login_stays_protected_while_redis_is_down(
    client: httpx.AsyncClient, upstash: FakeUpstash
) -> None:
    upstash.down = True

    statuses = [(await _login(client, "owner@school.org")).status_code for _ in range(6)]
    assert statuses == [401] * 5 + [429]


async def test_a_school_sharing_one_ip_can_still_sign_in(client: httpx.AsyncClient) -> None:
    # 15 staff behind the school's NAT, each mistyping once, then signing in.
    for n in range(10):
        await _login(client, f"teacher{n}@school.org", ip="203.0.113.50")
    assert (await _login(client, "owner@school.org", PASSWORD, ip="203.0.113.50")).status_code == (
        200
    )


# ── Authenticated APIs ───────────────────────────────────────────────────────
def _db_with_user(user_id: str) -> Mock:
    db = Mock()
    db.get = AsyncMock(return_value=SimpleNamespace(id=user_id))
    return db


async def test_each_user_gets_their_own_api_budget(upstash: FakeUpstash) -> None:
    alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
    db = _db_with_user(alice)

    for _ in range(120):
        await auth.get_current_user(Mock(), SimpleNamespace(id=alice), db, Mock())
    with pytest.raises(HTTPException) as error:
        await auth.get_current_user(Mock(), SimpleNamespace(id=alice), db, Mock())

    assert error.value.status_code == 429
    assert error.value.headers is not None and "Retry-After" in error.value.headers
    assert db.get.await_count == 120  # the blocked request never reached the database
    # Bob, on the same school network, is unaffected.
    await auth.get_current_user(Mock(), SimpleNamespace(id=bob), _db_with_user(bob), Mock())


async def test_api_requests_carry_on_while_redis_is_down(upstash: FakeUpstash) -> None:
    upstash.down = True
    user_id = str(uuid.uuid4())
    for _ in range(200):
        await auth.get_current_user(
            Mock(), SimpleNamespace(id=user_id), _db_with_user(user_id), Mock()
        )


# ── Invites ──────────────────────────────────────────────────────────────────
def _actor(tenant_id: uuid.UUID) -> Any:
    return SimpleNamespace(user_id=uuid.uuid4(), tenant_id=tenant_id)


async def _invite(actor: Any) -> int:
    with (
        patch.object(users.team, "get_manager", AsyncMock(return_value=actor)),
        patch.object(users.team, "invite_member", AsyncMock(return_value="ok")),
    ):
        try:
            await users.invite_team_member(Mock(), Mock(), Mock())
        except HTTPException as exc:
            return exc.status_code
    return 201


async def test_each_administrator_can_send_ten_invites_an_hour(upstash: FakeUpstash) -> None:
    admin = _actor(uuid.uuid4())
    assert [await _invite(admin) for _ in range(11)] == [201] * 10 + [429]


async def test_a_school_cannot_dodge_the_invite_limit_with_more_admins(
    upstash: FakeUpstash,
) -> None:
    school, other_school = uuid.uuid4(), uuid.uuid4()
    for _ in range(3):
        admin = _actor(school)
        assert [await _invite(admin) for _ in range(10)] == [201] * 10

    assert await _invite(_actor(school)) == 429
    assert await _invite(_actor(other_school)) == 201


async def test_resending_an_invite_counts_too(upstash: FakeUpstash) -> None:
    admin = _actor(uuid.uuid4())
    for _ in range(10):
        await _invite(admin)
    with (
        patch.object(users.team, "get_manager", AsyncMock(return_value=admin)),
        patch.object(users.team, "resend_invite", AsyncMock()) as resend,
        pytest.raises(HTTPException) as error,
    ):
        await users.resend_team_invite(uuid.uuid4(), Mock(), Mock())

    assert error.value.status_code == 429
    resend.assert_not_awaited()


# ── Registration and account emails ──────────────────────────────────────────
def _account_db(*, invited: bool = False) -> Mock:
    result = Mock()
    result.first.return_value = None  # no existing account
    db = Mock()
    db.execute = AsyncMock(return_value=result)
    db.scalar = AsyncMock(return_value=uuid.uuid4() if invited else None)
    return db


def _supabase() -> Mock:
    supabase = Mock()
    supabase.auth.admin.generate_link = Mock(
        return_value=SimpleNamespace(properties=SimpleNamespace(hashed_token="h"))
    )
    return supabase


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    deliver = AsyncMock()
    monkeypatch.setattr(account, "_deliver", deliver)
    monkeypatch.setattr(
        account, "get_settings", lambda: SimpleNamespace(FRONTEND_URL="https://app.test")
    )
    return deliver


def _signup(n: int, next_path: str | None = None) -> RegisterRequest:
    return RegisterRequest(
        first_name="A",
        last_name="B",
        email=f"teacher{n}@school.org",
        password=PASSWORD,
        next=next_path,
    )


async def _register(payload: RegisterRequest, db: Mock, ip: str = "203.0.113.50") -> int:
    try:
        await account.register(db, _supabase(), payload, ip)
    except HTTPException as exc:
        return exc.status_code
    return 202


async def test_three_new_accounts_per_ip_per_hour(upstash: FakeUpstash, sent: AsyncMock) -> None:
    statuses = [await _register(_signup(n), _account_db()) for n in range(4)]
    assert statuses == [202, 202, 202, 429]
    assert sent.await_count == 3
    assert await _register(_signup(9), _account_db(), ip="198.51.100.1") == 202


async def test_invited_staff_can_sign_up_together_from_the_school_network(
    upstash: FakeUpstash, sent: AsyncMock
) -> None:
    db = _account_db(invited=True)
    statuses = [await _register(_signup(n, f"/accept-invite?token=tok{n}"), db) for n in range(15)]
    assert statuses == [202] * 15
    # The token and the email were both checked against a pending invite.
    query = str(db.scalar.await_args.args[0])
    assert "invites.token" in query and "invites.email" in query


async def test_a_made_up_invite_token_gets_the_normal_limit(
    upstash: FakeUpstash, sent: AsyncMock
) -> None:
    db = _account_db(invited=False)
    statuses = [await _register(_signup(n, "/accept-invite?token=guess"), db) for n in range(4)]
    assert statuses == [202, 202, 202, 429]


async def test_next_paths_without_an_invite_token_skip_the_lookup(
    upstash: FakeUpstash, sent: AsyncMock
) -> None:
    db = _account_db()
    await _register(_signup(1, "/get-started"), db)
    await _register(_signup(2, "/elsewhere?token=x"), db)
    db.scalar.assert_not_awaited()


async def test_confirmation_emails_to_one_address_are_capped(
    upstash: FakeUpstash, sent: AsyncMock
) -> None:
    # Signup and resend share the address's budget, wherever the requests come from.
    assert await _register(_signup(1), _account_db(), ip="1.1.1.1") == 202
    for ip in ["2.2.2.2", "3.3.3.3"]:
        await account.resend_verification(
            _account_db(), _supabase(), EmailRequest(email="teacher1@school.org"), ip
        )
    with pytest.raises(HTTPException) as error:
        await account.resend_verification(
            _account_db(), _supabase(), EmailRequest(email="teacher1@school.org"), "4.4.4.4"
        )
    assert error.value.status_code == 429


@pytest.mark.parametrize("exists", [True, False])
async def test_password_resets_are_capped_per_account_whether_or_not_it_exists(
    upstash: FakeUpstash, sent: AsyncMock, exists: bool
) -> None:
    result = Mock()
    result.first.return_value = (True,) if exists else None
    db = Mock()
    db.execute = AsyncMock(return_value=result)
    payload = EmailRequest(email="owner@school.org")

    for n in range(3):
        await account.forgot_password(db, _supabase(), payload, f"198.51.100.{n}")
    with pytest.raises(HTTPException) as error:
        await account.forgot_password(db, _supabase(), payload, "198.51.100.9")

    assert error.value.status_code == 429
    assert sent.await_count == (3 if exists else 0)
