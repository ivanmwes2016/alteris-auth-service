from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlparse

import pytest
from scripts import resend_school_reviews as script

from app.services import school_approval


def _settings(**overrides: str | None) -> SimpleNamespace:
    values = {
        "ADMIN_NOTIFY_EMAILS": "admin@skolarly.net",
        "RESEND_API_KEY": "re_test",
        "EMAIL_FROM": "Skolarly <hello@skolarly.net>",
        "FRONTEND_URL": "https://app.skolarly.net",
    }
    return SimpleNamespace(**(values | overrides))


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    current = _settings()
    monkeypatch.setattr(script, "get_settings", lambda: current)
    monkeypatch.setattr(school_approval, "get_settings", lambda: current)
    return current


def test_ready_to_send_with_live_settings(settings: SimpleNamespace) -> None:
    assert script.problems() == []


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("ADMIN_NOTIFY_EMAILS", None),
        ("ADMIN_NOTIFY_EMAILS", " "),
        ("RESEND_API_KEY", None),
        ("FRONTEND_URL", "http://localhost:3000"),
        ("FRONTEND_URL", "https://localhost:3000"),
        ("FRONTEND_URL", None),
    ],
)
def test_refuses_when_nobody_gets_the_links_or_they_would_not_open(
    settings: SimpleNamespace, setting: str, value: str | None
) -> None:
    setattr(settings, setting, value)
    assert len(script.problems()) == 1


async def test_each_school_gets_a_new_working_link_saved_before_it_is_emailed(
    settings: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    db = Mock()
    db.commit = AsyncMock(side_effect=lambda: events.append("commit"))
    notify = AsyncMock(side_effect=lambda **kw: events.append("email"))
    monkeypatch.setattr(script, "notify_school_registered", notify)

    school = SimpleNamespace(
        name="Glory Nursery School",
        country="Uganda",
        plan="free",
        workspace_id="org-1",
        phone="+256 700 123456",
        school_type="Nursery",
        referral_source=None,
        review_token_hash="old",
    )
    owner = SimpleNamespace(name="Ivan Mwesigwa", email="owner@x.io")

    await script.resend(db, [(school, owner)])

    assert events == ["commit", "email"]
    sent = notify.await_args.kwargs
    assert sent["school_name"] == "Glory Nursery School"
    assert sent["owner_email"] == "owner@x.io"
    assert sent["phone"] == "+256 700 123456" and sent["school_type"] == "Nursery"
    link = urlparse(sent["review_url"])
    assert f"{link.scheme}://{link.hostname}" == "https://app.skolarly.net"
    (token,) = parse_qs(link.query)["token"]
    assert school.review_token_hash == school_approval._hash(token) != "old"
