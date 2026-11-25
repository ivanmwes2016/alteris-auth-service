import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException, status

from app.crud import parent_access, performance_release
from app.db.schemas.parent_access import TOPICS, ReleaseKey, ReleaseRead
from app.services import parent_push
from app.services.email import parent_invite_email

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
CHILD = uuid.uuid4()


# ---------------------------------------------------------------------------
# App status, as the School Hub shows it on each parent
# ---------------------------------------------------------------------------
def test_a_linked_login_is_active_whatever_the_invite() -> None:
    assert (
        parent_access.app_status(user_id=uuid.uuid4(), invite_expires_at=None, now=NOW) == "active"
    )


def test_no_login_and_no_invite_is_not_invited() -> None:
    assert parent_access.app_status(user_id=None, invite_expires_at=None, now=NOW) == "not_invited"


def test_an_invite_is_waiting_until_it_expires() -> None:
    later = NOW + timedelta(days=1)
    earlier = NOW - timedelta(seconds=1)
    assert parent_access.app_status(user_id=None, invite_expires_at=later, now=NOW) == "invited"
    assert parent_access.app_status(user_id=None, invite_expires_at=earlier, now=NOW) == "expired"


def test_clearing_access_unlinks_the_login_and_drops_the_invite() -> None:
    parent = SimpleNamespace(
        user_id=uuid.uuid4(),
        invite_token_hash="h",
        invite_expires_at=NOW,
        invited_at=NOW,
        joined_at=NOW,
    )
    parent_access.clear_access(parent)  # type: ignore[arg-type]
    assert parent.user_id is None
    assert parent.invite_token_hash is None
    assert parent.invite_expires_at is None
    assert parent.joined_at is None


# ---------------------------------------------------------------------------
# Small rules
# ---------------------------------------------------------------------------
def test_tokens_are_stored_hashed() -> None:
    hashed = parent_access.hash_token("secret-token")
    assert hashed != "secret-token"
    assert hashed == parent_access.hash_token("secret-token")


def test_name_parts_split_first_from_the_rest() -> None:
    assert parent_access.name_parts("  Grace   Akello Nanyonga ") == ("Grace", "Akello Nanyonga")
    assert parent_access.name_parts("Grace") == ("Grace", "")


def test_no_settings_row_means_every_push_is_on() -> None:
    assert all(parent_access.wants(None, topic) for topic in TOPICS)


def test_a_topic_turned_off_isnt_wanted() -> None:
    settings = SimpleNamespace(attendance=False, results=True, health=True, behaviour=True)
    assert not parent_access.wants(settings, "attendance")  # type: ignore[arg-type]
    assert parent_access.wants(settings, "health")  # type: ignore[arg-type]


def test_invite_link_opens_the_web_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        parent_access, "get_settings", lambda: SimpleNamespace(FRONTEND_URL="https://hub.test/")
    )
    assert parent_access.build_invite_url("tok") == "https://hub.test/parent-invite?token=tok"


def test_invite_email_names_the_school_and_children() -> None:
    subject, html_body, text_body = parent_invite_email(
        invite_url="https://hub.test/parent-invite?token=tok",
        school_name="Kampala High",
        children=["Amina", "Brian", "Cate"],
        first_name="Grace",
    )
    assert "Kampala High" in subject
    assert "Amina, Brian and Cate" in text_body
    assert "Hi Grace," in text_body
    assert "https://hub.test/parent-invite?token=tok" in html_body


# ---------------------------------------------------------------------------
# Inviting (the school's side)
# ---------------------------------------------------------------------------
def _caller() -> Any:
    return SimpleNamespace(tenant_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_a_parent_without_an_email_cant_be_invited() -> None:
    parent = SimpleNamespace(id=uuid.uuid4(), email="  ", name="Grace")
    with (
        patch.object(parent_access, "_school_parent", AsyncMock(return_value=parent)),
        pytest.raises(HTTPException) as error,
    ):
        await parent_access.invite(Mock(), _caller(), parent.id)
    assert error.value.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_a_parent_with_no_children_cant_be_invited() -> None:
    parent = SimpleNamespace(id=uuid.uuid4(), email="grace@example.com", name="Grace")
    with (
        patch.object(parent_access, "_school_parent", AsyncMock(return_value=parent)),
        patch.object(parent_access, "_children_names", AsyncMock(return_value=[])),
        pytest.raises(HTTPException) as error,
    ):
        await parent_access.invite(Mock(), _caller(), parent.id)
    assert error.value.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_inviting_stores_only_the_hash_and_emails_the_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        parent_access, "get_settings", lambda: SimpleNamespace(FRONTEND_URL="https://hub.test")
    )
    parent = SimpleNamespace(
        id=uuid.uuid4(),
        email="Grace@Example.com",
        name="Grace Akello",
        user_id=None,
        invite_token_hash=None,
        invite_expires_at=None,
        invited_at=None,
        joined_at=None,
    )
    db = Mock(scalar=AsyncMock(return_value="Kampala High"), flush=AsyncMock(), commit=AsyncMock())
    send = AsyncMock()
    with (
        patch.object(parent_access, "_school_parent", AsyncMock(return_value=parent)),
        patch.object(parent_access, "_children_names", AsyncMock(return_value=["Amina"])),
        patch.object(parent_access, "send_email", send),
    ):
        result = await parent_access.invite(db, _caller(), parent.id)

    assert result.status == "invited"
    assert parent.email == "grace@example.com"
    sent = send.await_args.kwargs
    assert sent["to"] == "grace@example.com"
    token = sent["text_body"].split("parent-invite?token=")[1].split()[0]
    assert parent.invite_token_hash == parent_access.hash_token(token)
    db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Accepting (the invite page)
# ---------------------------------------------------------------------------
def _invited() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        email="grace@example.com",
        name="Grace Akello",
        user_id=None,
        invite_token_hash="h",
        invite_expires_at=NOW + timedelta(days=3),
        joined_at=None,
    )


def _accept_db() -> Mock:
    return Mock(scalar=AsyncMock(return_value="Kampala High"), commit=AsyncMock())


@pytest.mark.asyncio
async def test_accepting_without_a_login_creates_a_confirmed_one_and_links_it() -> None:
    parent = _invited()
    new_id = uuid.uuid4()
    supabase = Mock()
    supabase.auth.admin.create_user.return_value = SimpleNamespace(
        user=SimpleNamespace(id=str(new_id))
    )
    with (
        patch.object(parent_access, "_invited_parent", AsyncMock(return_value=parent)),
        patch.object(parent_access, "_login", AsyncMock(return_value=None)),
        patch.object(parent_access, "_ensure_user_row", AsyncMock()),
    ):
        result = await parent_access.accept_invite(
            _accept_db(), supabase, "tok", "a long enough passphrase"
        )

    created = supabase.auth.admin.create_user.call_args.args[0]
    assert created["email"] == "grace@example.com"
    assert created["email_confirm"] is True
    assert created["user_metadata"] == {"first_name": "Grace", "last_name": "Akello"}
    assert result.account_created
    assert parent.user_id == new_id
    assert parent.invite_token_hash is None  # the link can't be used again
    assert parent.joined_at is not None


@pytest.mark.asyncio
async def test_accepting_without_a_login_needs_a_password() -> None:
    with (
        patch.object(parent_access, "_invited_parent", AsyncMock(return_value=_invited())),
        patch.object(parent_access, "_login", AsyncMock(return_value=None)),
        pytest.raises(HTTPException) as error,
    ):
        await parent_access.accept_invite(_accept_db(), Mock(), "tok", None)
    assert error.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_a_confirmed_login_is_linked_as_it_is() -> None:
    parent = _invited()
    existing = uuid.uuid4()
    supabase = Mock()
    with (
        patch.object(parent_access, "_invited_parent", AsyncMock(return_value=parent)),
        patch.object(parent_access, "_login", AsyncMock(return_value=(existing, True))),
        patch.object(parent_access, "_ensure_user_row", AsyncMock()),
    ):
        result = await parent_access.accept_invite(_accept_db(), supabase, "tok", None)

    supabase.auth.admin.create_user.assert_not_called()
    supabase.auth.admin.update_user_by_id.assert_not_called()
    assert not result.account_created
    assert parent.user_id == existing


@pytest.mark.asyncio
async def test_an_unconfirmed_login_gets_the_new_password_and_is_confirmed() -> None:
    parent = _invited()
    existing = uuid.uuid4()
    supabase = Mock()
    with (
        patch.object(parent_access, "_invited_parent", AsyncMock(return_value=parent)),
        patch.object(parent_access, "_login", AsyncMock(return_value=(existing, False))),
        patch.object(parent_access, "_ensure_user_row", AsyncMock()),
    ):
        await parent_access.accept_invite(_accept_db(), supabase, "tok", "a long enough passphrase")

    user_id, changes = supabase.auth.admin.update_user_by_id.call_args.args
    assert user_id == str(existing)
    assert changes["email_confirm"] is True
    assert parent.user_id == existing


@pytest.mark.asyncio
async def test_an_unknown_or_expired_token_is_refused() -> None:
    expired = _invited()
    expired.invite_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    for found in (None, expired):
        db = Mock(scalar=AsyncMock(return_value=found))
        with pytest.raises(HTTPException) as error:
            await parent_access.accept_invite(db, Mock(), "tok", "x")
        assert error.value.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# A parent's children
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_student_who_isnt_their_child_is_not_found() -> None:
    result = Mock()
    result.first.return_value = None
    db = Mock(execute=AsyncMock(return_value=result))
    with pytest.raises(HTTPException) as error:
        await parent_access.require_child(db, SimpleNamespace(id=uuid.uuid4()), uuid.uuid4())  # type: ignore[arg-type]
    assert error.value.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# Pushes
# ---------------------------------------------------------------------------
def _attendance(status_: str, previous: str | None, day: date = date(2026, 11, 23)) -> Any:
    return parent_push.attendance_notice(
        student_id=CHILD,
        student_name="Amina Nakato",
        status=status_,
        previous=previous,
        day=day,
        today=date(2026, 11, 23),
    )


def test_late_and_unexcused_marks_notify_parents() -> None:
    late = _attendance("Late", None)
    absent = _attendance("Unexcused", "Present")
    assert late is not None and late.title == "Amina was marked late"
    assert absent is not None and absent.title == "Amina was marked absent"
    assert "today" in absent.body
    assert absent.topic == "attendance"
    assert absent.data == {
        "kind": "parent",
        "topic": "attendance",
        "studentId": str(CHILD),
        "screen": "attendance",
    }


@pytest.mark.parametrize(("status_", "previous"), [("Present", None), ("Excused", None)])
def test_present_and_excused_marks_dont_notify(status_: str, previous: str | None) -> None:
    assert _attendance(status_, previous) is None


def test_a_mark_that_didnt_change_doesnt_notify_again() -> None:
    assert _attendance("Late", "Late") is None


def test_an_earlier_days_register_names_the_day() -> None:
    notice = _attendance("Late", None, day=date(2026, 11, 20))
    assert notice is not None
    assert "on Friday 20 November" in notice.body


def test_sick_bay_notices() -> None:
    sent = parent_push.sick_bay_sent_notice(
        student_id=CHILD, student_name="Amina", complaint="Headache"
    )
    assert sent.title == "Amina is at the sick bay" and sent.topic == "health"
    home = parent_push.sick_bay_closed_notice(
        student_id=CHILD, student_name="Amina", outcome="home", complaint="Fever"
    )
    assert home is not None and home.title == "Amina is being sent home"
    hospital = parent_push.sick_bay_closed_notice(
        student_id=CHILD, student_name="Amina", outcome="hospital", complaint="Fever"
    )
    assert hospital is not None and "hospital" in hospital.title
    assert (
        parent_push.sick_bay_closed_notice(
            student_id=CHILD, student_name="Amina", outcome="back", complaint="Fever"
        )
        is None
    )


def test_only_new_incidents_and_recognitions_notify() -> None:
    old_incident, new_incident, new_award = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    profile = SimpleNamespace(
        incidents=[
            SimpleNamespace(id=old_incident, name="Late homework", severity="Mild"),
            SimpleNamespace(id=new_incident, name="Fighting", severity="Severe"),
        ],
        recognitions=[SimpleNamespace(id=new_award, name="Star of the week")],
    )
    student = SimpleNamespace(id=CHILD, name="Amina Nakato")
    notices = parent_push.new_behaviour_notices(student, profile, known={old_incident})  # type: ignore[arg-type]
    assert [n.title for n in notices] == ["Behaviour report for Amina", "Amina was recognised"]
    assert notices[0].body == "Fighting (severe)"
    assert all(n.topic == "behaviour" for n in notices)


@pytest.mark.asyncio
async def test_pushes_go_only_to_parents_who_want_them() -> None:
    wanting = uuid.uuid4()
    notice = parent_push.sick_bay_sent_notice(
        student_id=CHILD, student_name="Amina", complaint="Headache"
    )
    other = parent_push.sick_bay_sent_notice(
        student_id=uuid.uuid4(), student_name="Brian", complaint="Cough"
    )
    session = Mock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    send = AsyncMock()
    with (
        patch.object(parent_push, "SessionLocal", Mock(return_value=session)),
        patch.object(
            parent_push.parent_access,
            "recipients",
            AsyncMock(return_value={CHILD: {wanting}}),
        ),
        patch.object(parent_push, "tokens_for", AsyncMock(return_value=["ExponentPushToken[a]"])),
        patch.object(parent_push, "send_push", send),
    ):
        await parent_push.notify_parents([notice, other])

    send.assert_awaited_once()
    assert send.await_args.args[0] == ["ExponentPushToken[a]"]
    assert send.await_args.kwargs["channel"] == "parents"


@pytest.mark.asyncio
async def test_a_failure_sending_never_raises() -> None:
    notice = parent_push.sick_bay_sent_notice(student_id=CHILD, student_name="A", complaint="x")
    with patch.object(parent_push, "SessionLocal", Mock(side_effect=RuntimeError("db down"))):
        await parent_push.notify_parents([notice])


# ---------------------------------------------------------------------------
# Term marks published to parents
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("score", "value"),
    [("78", 78.0), (" 64.5 ", 64.5), ("A", None), ("", None), ("nan", None), ("inf", None)],
)
def test_only_finite_numbers_count_towards_averages(score: str, value: float | None) -> None:
    assert performance_release.numeric(score) == value


def test_averages_round_to_one_place_and_need_a_score() -> None:
    assert performance_release.mean([70.0, 75.0, 81.0]) == 75.3
    assert performance_release.mean([]) is None


def test_results_notice() -> None:
    notice = parent_push.results_notice(
        student_id=CHILD, student_name="Amina Nakato", term="Term 3", year=2026
    )
    assert notice.title == "Amina's Term 3 2026 results are out"
    assert notice.topic == "results"
    assert notice.data["screen"] == "results"


def _release_db(inserted: uuid.UUID | None) -> Mock:
    return Mock(scalar=AsyncMock(return_value=inserted), commit=AsyncMock())


@pytest.mark.asyncio
async def test_publishing_tells_the_parents_who_want_results() -> None:
    key = ReleaseKey(class_name="Primary 1 A", term="Term 3", year=2026)
    told, quiet = uuid.uuid4(), uuid.uuid4()
    parent = uuid.uuid4()
    background = Mock()
    released = ReleaseRead(**key.model_dump(), released=True, released_at=NOW)
    with (
        patch.object(performance_release, "get_release", AsyncMock(return_value=released)),
        patch.object(
            performance_release,
            "_students_with_marks",
            AsyncMock(return_value=[(told, "Amina Nakato"), (quiet, "Brian Okello")]),
        ),
        patch.object(
            performance_release.parent_access,
            "recipients",
            AsyncMock(return_value={told: {parent}}),
        ),
    ):
        result = await performance_release.publish(
            _release_db(uuid.uuid4()), _caller(), SimpleNamespace(id=uuid.uuid4()), key, background
        )

    assert result.parents_notified == 1
    task, notices = background.add_task.call_args.args
    assert task is parent_push.notify_parents
    assert [n.student_id for n in notices] == [told]


@pytest.mark.asyncio
async def test_publishing_again_doesnt_notify_twice() -> None:
    key = ReleaseKey(class_name="Primary 1 A", term="Term 3", year=2026)
    background = Mock()
    released = ReleaseRead(**key.model_dump(), released=True, released_at=NOW)
    with patch.object(performance_release, "get_release", AsyncMock(return_value=released)):
        result = await performance_release.publish(
            _release_db(None), _caller(), SimpleNamespace(id=uuid.uuid4()), key, background
        )
    assert result.released
    background.add_task.assert_not_called()


def test_same_email_ignores_case_spaces_and_blanks() -> None:
    assert parent_access.same_email("Grace@Example.com ", "grace@example.com")
    assert parent_access.same_email(None, "  ")
    assert not parent_access.same_email("grace@example.com", "g.akello@example.com")
    assert not parent_access.same_email(None, "grace@example.com")
