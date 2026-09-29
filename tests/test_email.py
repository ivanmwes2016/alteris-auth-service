import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.services import email


def _settings(key: str | None = "re_test", sender: str | None = "School <hi@x.io>") -> Any:
    return lambda: SimpleNamespace(RESEND_API_KEY=key, EMAIL_FROM=sender)


def _transport(status_code: int, seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status_code, json={"id": "em_1"})

    return httpx.MockTransport(handler)


def _use(monkeypatch: pytest.MonkeyPatch, status_code: int, seen: list[httpx.Request]) -> None:
    real = httpx.AsyncClient
    monkeypatch.setattr(
        email.httpx,
        "AsyncClient",
        lambda **kw: real(transport=_transport(status_code, seen), **kw),
    )


async def _send() -> None:
    await email.send_email(to="a@b.com", subject="S", html_body="<p>H</p>", text_body="T")


@pytest.mark.asyncio
async def test_sends_through_resend_with_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []
    monkeypatch.setattr(email, "get_settings", _settings())
    _use(monkeypatch, 200, seen)

    await _send()

    (request,) = seen
    assert str(request.url) == email.RESEND_URL
    assert request.headers["authorization"] == "Bearer re_test"
    body = json.loads(request.content)
    assert body["to"] == ["a@b.com"] and body["from"] == "School <hi@x.io>"


@pytest.mark.asyncio
async def test_rate_limit_is_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email, "get_settings", _settings())
    _use(monkeypatch, 429, [])

    with pytest.raises(email.EmailError) as error:
        await _send()
    assert error.value.rate_limited


@pytest.mark.asyncio
async def test_rejection_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email, "get_settings", _settings())
    _use(monkeypatch, 403, [])

    with pytest.raises(email.EmailError) as error:
        await _send()
    assert not error.value.rate_limited


@pytest.mark.asyncio
async def test_missing_configuration_fails_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []
    monkeypatch.setattr(email, "get_settings", _settings(key=None))
    _use(monkeypatch, 200, seen)

    with pytest.raises(email.EmailError):
        await _send()
    assert seen == []
