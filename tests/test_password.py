# Full-width and Cyrillic letters below are deliberate: they test normalization.
# ruff: noqa: RUF001
import hashlib
import logging
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI, HTTPException, status
from gotrue.errors import AuthApiError
from pydantic import ValidationError

from app.api.v1.routes import auth
from app.db.models.auth import RegisterRequest
from app.helpers import password
from app.services import account
from app.services.auth_service import AuthService

# conftest swaps this out so no test reaches the real service; kept for the tests below.
REAL_RANGE = password._range

EMAIL = "ivanmwesigwa@example.com"


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest().upper()


def _pwned(monkeypatch: pytest.MonkeyPatch, *breached: str) -> list[str]:
    """Pwned Passwords answering with `breached` in its results; returns prefixes asked."""
    asked: list[str] = []

    async def fake_range(prefix: str) -> str:
        asked.append(prefix)
        lines = ["0018A45C4D1DEF81644B54AB7F969B88D65:1"]
        lines += [f"{h[5:]}:42" for h in map(_sha1, breached) if h.startswith(prefix)]
        return "\r\n".join(lines)

    monkeypatch.setattr(password, "_range", fake_range)
    return asked


async def _problem(value: str, email: str | None = EMAIL) -> str | None:
    try:
        await password.check(password.normalize(value), email)
    except HTTPException as exc:
        assert exc.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        return str(exc.detail)
    return None


# ── Length ───────────────────────────────────────────────────────────────────
async def test_seven_characters_are_too_short() -> None:
    assert await _problem("kx9#Lm2") == password.TOO_SHORT


async def test_a_strong_eight_character_password_is_accepted() -> None:
    assert await _problem("gH7!kq2Z") is None


async def test_length_counts_characters_not_bytes() -> None:
    # 7 emoji are 28 bytes but only 7 characters.
    assert await _problem("🦊🌲🚲🎻🍋🛶🧭") == password.TOO_SHORT


async def test_a_64_character_passphrase_with_spaces_is_accepted() -> None:
    passphrase = "violet tram hums past the quiet bakery at dawn while gulls argue"
    assert len(passphrase) == 64
    assert await _problem(passphrase) is None


async def test_over_72_bytes_is_rejected_never_truncated() -> None:
    base = "maple quartz lantern " * 4
    assert await _problem(base[:72]) is None
    assert await _problem(base[:73]) == password.TOO_LONG
    # 19 emoji are 76 bytes.
    assert await _problem("🦊🌲🚲🎻🍋🛶🧭🪁🐙🌵🎈🧩🦉🍉🚀🎲🪴🦀🧁") == password.TOO_LONG


# ── Any characters ───────────────────────────────────────────────────────────
async def test_an_emoji_password_is_accepted() -> None:
    assert await _problem("🦊🌲🚲🎻🍋🛶🧭🪁") is None


async def test_no_composition_rules() -> None:
    assert await _problem("ночной дождь") is None


# ── Normalization ────────────────────────────────────────────────────────────
async def test_nfkc_makes_full_width_letters_match_their_plain_form() -> None:
    assert password.normalize("ｐａｓｓｗｏｒｄ") == "password"
    assert await _problem("ｐａｓｓｗｏｒｄ") == password.COMMON


def test_both_ways_of_typing_an_accent_are_one_password() -> None:
    assert password.normalize("café au lait") == password.normalize("café au lait")


# ── Blocklists ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("value", ["password", "football", "Sunshine", "elephant"])
async def test_a_common_word_or_password_on_its_own_is_rejected(value: str) -> None:
    assert await _problem(value) == password.COMMON


async def test_common_words_combined_are_fine() -> None:
    assert await _problem("football elephant sunshine") is None


@pytest.mark.parametrize(
    "value",
    [
        "aaaaaaaa",
        "12345678",
        "abcdefgh",
        "qwertyui",
        "87654321",
        "abababab",
        "1234567890",
        "LKJHGFDS",
    ],
)
async def test_repeated_or_sequential_characters_are_rejected(value: str) -> None:
    assert await _problem(value) == password.SEQUENCE


async def test_a_password_containing_the_username_is_rejected() -> None:
    # No usernames here: the email's local part stands in for one.
    assert await _problem("IvanMwesigwa rocks!") == password.PERSONAL


async def test_very_short_email_names_are_ignored() -> None:
    assert await _problem("jo rides the night bus", "jo@example.com") is None


async def test_a_password_containing_the_app_name_is_rejected() -> None:
    assert await _problem("I love Skolarly!") == password.APP_NAME


# ── Breaches ─────────────────────────────────────────────────────────────────
async def test_password123_a_breached_password_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pwned(monkeypatch, "password123")
    assert await _problem("password123") is not None


async def test_a_password_found_in_a_breach_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _pwned(monkeypatch, "violet tram hums")
    assert await _problem("violet tram hums") == password.BREACHED
    assert asked == [_sha1("violet tram hums")[:5]]


async def test_padding_entries_are_not_breaches(monkeypatch: pytest.MonkeyPatch) -> None:
    suffix = _sha1("violet tram hums")[5:]

    async def padded(prefix: str) -> str:
        return f"{suffix}:0\r\n"

    monkeypatch.setattr(password, "_range", padded)
    assert await password.is_breached("violet tram hums") is False


async def test_only_the_hash_prefix_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="")

    real = httpx.AsyncClient
    monkeypatch.setattr(password, "_range", REAL_RANGE)
    monkeypatch.setattr(
        password.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handle), **kw),
    )

    assert await password.is_breached("violet tram hums") is False

    (request,) = seen
    digest = _sha1("violet tram hums")
    assert str(request.url) == f"https://api.pwnedpasswords.com/range/{digest[:5]}"
    assert request.headers["Add-Padding"] == "true"
    assert digest[5:] not in str(request.url) and not request.content
    assert "violet" not in str(request.url)


async def test_fails_open_and_logs_when_unreachable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def down(prefix: str) -> str:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(password, "_range", down)
    with caplog.at_level(logging.ERROR):
        assert await _problem("violet tram hums") is None
    assert "Pwned Passwords check failed" in caplog.text
    assert "violet" not in caplog.text


# ── Sign-up ──────────────────────────────────────────────────────────────────
def _register(value: str) -> RegisterRequest:
    return RegisterRequest(first_name="Ivan", last_name="Mwesigwa", email=EMAIL, password=value)


def test_signup_reports_the_rule_that_failed() -> None:
    with pytest.raises(ValidationError) as error:
        _register("ivanmwesigwa1")
    assert password.PERSONAL in str(error.value)


def test_signup_stores_the_normalized_password() -> None:
    assert _register("ｖｉｏｌｅｔ ｔｒａｍ").password == "violet tram"


async def test_signup_with_a_breached_password_creates_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pwned(monkeypatch, "violet tram hums")
    monkeypatch.setattr(account.rate_limit, "enforce", AsyncMock())
    supabase = Mock()

    with pytest.raises(HTTPException) as error:
        await account.register(Mock(), supabase, _register("violet tram hums"), "203.0.113.9")

    assert error.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert error.value.detail == password.BREACHED
    supabase.auth.admin.generate_link.assert_not_called()


# ── Changing a password ──────────────────────────────────────────────────────
USER_ID = uuid.uuid4()


@pytest.fixture
def supabase() -> Mock:
    return Mock()


@pytest.fixture
async def http(supabase: Mock) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(auth.router, prefix="/auth")
    app.dependency_overrides[auth.get_supabase_user] = lambda: SimpleNamespace(
        id=USER_ID, email=EMAIL
    )
    app.dependency_overrides[auth.get_supabase] = lambda: supabase
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as client:
        yield client


async def test_change_password_stores_the_normalized_password(
    http: httpx.AsyncClient, supabase: Mock
) -> None:
    response = await http.post("/auth/password", json={"password": "ｖｉｏｌｅｔ ｔｒａｍ"})

    assert response.status_code == status.HTTP_204_NO_CONTENT
    supabase.auth.admin.update_user_by_id.assert_called_once_with(
        str(USER_ID), {"password": "violet tram"}
    )


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("short", password.TOO_SHORT),
        ("12345678", password.SEQUENCE),
        ("ivanmwesigwa!!", password.PERSONAL),
        ("football", password.COMMON),
    ],
)
async def test_change_password_refuses_a_password_that_breaks_a_rule(
    http: httpx.AsyncClient, supabase: Mock, value: str, message: str
) -> None:
    response = await http.post("/auth/password", json={"password": value})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json()["detail"] == message
    supabase.auth.admin.update_user_by_id.assert_not_called()


async def test_change_password_refuses_a_breached_password(
    http: httpx.AsyncClient, supabase: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pwned(monkeypatch, "violet tram hums")
    response = await http.post("/auth/password", json={"password": "violet tram hums"})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json()["detail"] == password.BREACHED
    supabase.auth.admin.update_user_by_id.assert_not_called()


async def test_change_password_reports_supabase_outages(
    http: httpx.AsyncClient, supabase: Mock
) -> None:
    supabase.auth.admin.update_user_by_id.side_effect = AuthApiError("down", 500, None)
    response = await http.post("/auth/password", json={"password": "violet tram hums"})
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


# ── Signing in ───────────────────────────────────────────────────────────────
def _tokens() -> SimpleNamespace:
    return SimpleNamespace(
        user=SimpleNamespace(id="u"),
        session=SimpleNamespace(access_token="a", refresh_token="r", token_type="bearer"),
    )


async def test_login_sends_the_normalized_password() -> None:
    service = AuthService(supabase=Mock(), db=Mock())
    service.supabase.auth.sign_in_with_password.return_value = _tokens()

    await service.login(EMAIL, "ｖｉｏｌｅｔ ｔｒａｍ")

    sent = service.supabase.auth.sign_in_with_password.call_args.args[0]
    assert sent["password"] == "violet tram"


async def test_login_falls_back_to_a_password_stored_before_normalization() -> None:
    service = AuthService(supabase=Mock(), db=Mock())
    service.supabase.auth.sign_in_with_password.side_effect = [
        AuthApiError("Invalid login credentials", 400, "invalid_credentials"),
        _tokens(),
    ]

    result = await service.login(EMAIL, "ｖｉｏｌｅｔ ｔｒａｍ")

    assert result.access_token == "a"
    calls = service.supabase.auth.sign_in_with_password.call_args_list
    assert [c.args[0]["password"] for c in calls] == ["violet tram", "ｖｉｏｌｅｔ ｔｒａｍ"]
