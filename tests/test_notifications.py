from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import notifications
from app.services.email import EmailError, school_registered_email


def _settings(recipients: str | None) -> object:
    return lambda: SimpleNamespace(ADMIN_NOTIFY_EMAILS=recipients, FRONTEND_URL="https://app.test")


async def _notify() -> None:
    await notifications.notify_school_registered(
        school_name="Kinaawa High School",
        country="Uganda",
        plan="starter",
        workspace_id="org-abc",
        owner_name="Ivan Mwesigwa",
        owner_email="ivan@x.io",
        review_url="https://app.test/review-school?token=t",
    )


@pytest.mark.asyncio
async def test_every_admin_address_gets_the_alert() -> None:
    send = AsyncMock()
    with (
        patch.object(notifications, "get_settings", _settings(" a@x.io, b@x.io ,")),
        patch.object(notifications, "send_email", send),
    ):
        await _notify()

    assert [call.kwargs["to"] for call in send.await_args_list] == ["a@x.io", "b@x.io"]
    kwargs = send.await_args.kwargs
    assert kwargs["subject"] == "New school awaiting approval: Kinaawa High School"
    assert "https://app.test/review-school?token=t" in kwargs["text_body"]
    assert "Ivan Mwesigwa <ivan@x.io>" in kwargs["text_body"]
    assert "Uganda" in kwargs["text_body"] and "org-abc" in kwargs["text_body"]


@pytest.mark.asyncio
@pytest.mark.parametrize("recipients", [None, "", " , "])
async def test_no_recipients_means_no_email(recipients: str | None) -> None:
    send = AsyncMock()
    with (
        patch.object(notifications, "get_settings", _settings(recipients)),
        patch.object(notifications, "send_email", send),
    ):
        await _notify()

    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [EmailError("down"), RuntimeError("boom")])
async def test_a_failed_alert_never_raises_and_still_tries_the_rest(failure: Exception) -> None:
    send = AsyncMock(side_effect=[failure, None])
    with (
        patch.object(notifications, "get_settings", _settings("a@x.io,b@x.io")),
        patch.object(notifications, "send_email", send),
    ):
        await _notify()

    assert send.await_count == 2


def test_school_names_are_escaped_in_the_html() -> None:
    _, html_body, _ = school_registered_email(
        school_name="<script>x</script>",
        country=None,
        plan=None,
        workspace_id=None,
        owner="a@x.io",
        registered_at="now",
        review_url="https://app.test",
    )
    assert "<script>" not in html_body


@pytest.mark.asyncio
async def test_owner_is_thanked_and_told_the_school_is_pending() -> None:
    send = AsyncMock()
    with (
        patch.object(notifications, "get_settings", _settings(None)),
        patch.object(notifications, "send_email", send),
    ):
        await notifications.notify_owner_school_received(
            school_name="Kinaawa High School", owner_name="Ivan Mwesigwa", owner_email="ivan@x.io"
        )

    kwargs = send.await_args.kwargs
    assert kwargs["to"] == "ivan@x.io"
    assert "Kinaawa High School" in kwargs["subject"]
    body = kwargs["text_body"]
    assert body.startswith("Hi Ivan,")
    assert "Thank you for trying out Skolarly" in body
    assert "pending" in body and "approved" in body


@pytest.mark.asyncio
async def test_owner_email_failure_never_raises() -> None:
    with (
        patch.object(notifications, "get_settings", _settings(None)),
        patch.object(notifications, "send_email", AsyncMock(side_effect=EmailError("down"))),
    ):
        await notifications.notify_owner_school_received(
            school_name="X", owner_name=None, owner_email="a@x.io"
        )
