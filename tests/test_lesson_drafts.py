import json
from datetime import date, time
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from app.crud import lesson_drafts
from app.crud.lesson_drafts import (
    DraftContext,
    build_prompt,
    describe_classroom,
    drafts_left,
    estimated_cost,
    guess_curriculum,
    name_patterns,
    output_schema,
    parts_to_draft,
    redact,
)
from app.db.models.planning import PlanningAiSettings
from app.services import lesson_drafter
from app.services.lesson_drafter import DraftError, draft_parts


# ---------------------------------------------------------------------------
# Curriculum and classroom
# ---------------------------------------------------------------------------
def test_the_curriculum_is_guessed_from_the_country_and_class_names() -> None:
    uganda = guess_curriculum("Uganda", ["S.1", "S.2", "S.3"])
    assert "NCDC" in uganda and "secondary school" in uganda
    assert "primary school" in guess_curriculum("uganda", ["P.4", "P.5"])
    assert "Kenya" in guess_curriculum("Kenya", ["Grade 6"])
    assert guess_curriculum("Malawi", []) == "the national curriculum of Malawi"
    assert guess_curriculum(None, []) == "a national school curriculum"


def test_an_undescribed_classroom_assumes_a_large_class_and_basic_materials() -> None:
    lines = describe_classroom(None)
    assert "60 learners" in lines[0]
    assert any("no electricity" in line for line in lines)
    assert any("chalkboard" in line for line in lines)
    assert any("few or no textbooks" in line for line in lines)


def test_a_described_classroom_is_used_as_it_is() -> None:
    row = PlanningAiSettings(
        class_size=35, has_power=True, has_projector=True, textbooks="one_each"
    )
    assert describe_classroom(row) == [
        "About 35 learners in the class.",
        "There is electricity in the classroom.",
        "A projector is available.",
        "Every learner has a textbook.",
    ]


def test_a_projector_without_power_is_no_projector() -> None:
    row = PlanningAiSettings(has_power=False, has_projector=True)
    assert any("No projector" in line for line in describe_classroom(row))


# ---------------------------------------------------------------------------
# Student names never leave the server
# ---------------------------------------------------------------------------
def test_student_names_are_replaced_before_sending() -> None:
    patterns = name_patterns(["Brian Okello", "Aisha Nakato"])
    text = "Brian Okello and aisha still can't do long division; Nakato helped."
    assert redact(text, patterns) == (
        "a student and a student still can't do long division; a student helped."
    )


def test_names_inside_other_words_are_left_alone() -> None:
    patterns = name_patterns(["Ann Ali"])
    assert redact("Annual planning: alignment", patterns) == "Annual planning: alignment"


def test_very_short_name_parts_are_left_alone() -> None:
    patterns = name_patterns(["Jo Ng"])
    assert redact("Go on with the topic", patterns) == "Go on with the topic"


def test_empty_text_stays_empty() -> None:
    assert redact(None, name_patterns(["Brian"])) is None
    assert redact("", name_patterns(["Brian"])) == ""


# ---------------------------------------------------------------------------
# What gets drafted
# ---------------------------------------------------------------------------
def test_only_empty_parts_are_drafted() -> None:
    current = {
        "subtopic": "Equivalent fractions",
        "objectives": "Learners should be able to: find equivalent fractions",
        "materials": "  ",
        "introduction": None,
        "development": "",
        "assessment": None,
        "conclusion": "My own ending",
    }
    assert parts_to_draft(current) == ["materials", "introduction", "development", "assessment"]


def test_the_schema_asks_for_exactly_the_drafted_parts() -> None:
    schema = output_schema(["introduction", "assessment"])
    assert schema["required"] == ["introduction", "assessment"]
    assert schema["additionalProperties"] is False


def test_the_daily_limit_never_goes_below_zero() -> None:
    assert drafts_left(3, 20) == 17
    assert drafts_left(25, 20) == 0


def test_cost_is_estimated_from_the_models_prices() -> None:
    # 1M in + 1M out on Sonnet 5.5: $2 + $10.
    assert estimated_cost("claude-sonnet-5-5", 1_000_000, 1_000_000) == 12.0
    assert estimated_cost("claude-sonnet-5-5", 3_000, 1_500) == 0.021


def _context(**overrides: Any) -> DraftContext:
    values: dict[str, Any] = {
        "curriculum": "Uganda's national curriculum (NCDC)",
        "classroom": ["A large class: assume about 60 learners."],
        "class_name": "S.2",
        "streams": ["East", "West"],
        "subject": "Mathematics",
        "day": date(2026, 6, 10),
        "starts_at": time(8, 0),
        "minutes": 80,
        "week": 3,
        "lessons_in_plan": 2,
        "entry": {"topic": "Fractions", "objectives": "Find equivalent fractions", "lessons": 2},
        "previous": {"status": "Partly taught (carry on this lesson)", "reflection": "Struggled"},
        "written": {"topic": "Fractions"},
        "note": "group work",
        "parts": ["introduction", "development"],
    }
    values.update(overrides)
    return DraftContext(**values)


def test_the_prompt_carries_the_lesson_the_scheme_and_the_last_lesson() -> None:
    prompt = build_prompt(_context())
    assert "- Class: S.2 (East, West)" in prompt
    assert "- Length: 80 minutes" in prompt
    assert "- Term week: 3" in prompt
    assert "used for 2 lessons" in prompt
    assert "- Objectives: Find equivalent fractions" in prompt
    assert "- Recorded as: Partly taught" in prompt
    assert "The teacher's note: group work" in prompt
    assert prompt.endswith("Write these parts of the plan: introduction, development.")


def test_the_prompt_leaves_out_what_isnt_known() -> None:
    prompt = build_prompt(_context(entry=None, previous=None, note=None, week=None, minutes=0))
    assert "scheme of work" not in prompt
    assert "previous lesson" not in prompt
    assert "note" not in prompt
    assert "assume 40 minutes" in prompt


# ---------------------------------------------------------------------------
# The call to Claude
# ---------------------------------------------------------------------------
def _message(stop_reason: str = "end_turn", text: str = "{}") -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=1200, output_tokens=600),
        content=[SimpleNamespace(type="text", text=text)],
    )


def _fake_client(monkeypatch: pytest.MonkeyPatch, result: Any) -> dict[str, Any]:
    sent: dict[str, Any] = {}

    async def create(**kwargs: Any) -> Any:
        sent.update(kwargs)
        if isinstance(result, Exception):
            raise result
        return result

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    monkeypatch.setattr(lesson_drafter, "_anthropic", lambda: client)
    return sent


async def test_a_draft_comes_back_as_its_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = json.dumps({"introduction": "Ask: who has shared a chapati?", "development": "1. ..."})
    sent = _fake_client(monkeypatch, _message(text=reply))
    drafted = await draft_parts("claude-sonnet-5-5", "system", "prompt", output_schema(["x"]))
    assert drafted.parts["introduction"] == "Ask: who has shared a chapati?"
    assert (drafted.input_tokens, drafted.output_tokens) == (1200, 600)
    assert sent["model"] == "claude-sonnet-5-5"
    assert sent["fallbacks"] == "default"
    assert sent["output_config"]["format"]["type"] == "json_schema"


async def test_a_refusal_is_told_to_the_teacher_and_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_client(monkeypatch, _message(stop_reason="refusal"))
    with pytest.raises(DraftError) as raised:
        await draft_parts("claude-sonnet-5-5", "system", "prompt", {})
    assert raised.value.outcome == "refused"
    assert raised.value.status_code == 422
    assert raised.value.input_tokens == 1200


async def test_no_credit_or_a_bad_key_says_it_isnt_set_up(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(400, request=request)
    error = anthropic.BadRequestError("credit balance is too low", response=response, body=None)
    _fake_client(monkeypatch, error)
    with pytest.raises(DraftError) as raised:
        await draft_parts("claude-sonnet-5-5", "system", "prompt", {})
    assert raised.value.message == lesson_drafter.NOT_SET_UP
    assert raised.value.outcome == "failed"


async def test_an_unreachable_service_says_try_again(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    _fake_client(monkeypatch, anthropic.APIConnectionError(request=request))
    with pytest.raises(DraftError) as raised:
        await draft_parts("claude-sonnet-5-5", "system", "prompt", {})
    assert raised.value.message == lesson_drafter.BUSY


def test_only_the_owner_and_admins_can_switch_drafting() -> None:
    def viewer(is_owner: bool, role: str) -> Any:
        return SimpleNamespace(actor=SimpleNamespace(is_owner=is_owner, role_name=role))

    assert lesson_drafts.can_switch(viewer(True, "teacher"))
    assert lesson_drafts.can_switch(viewer(False, "admin"))
    assert not lesson_drafts.can_switch(viewer(False, "head_teacher"))
    assert not lesson_drafts.can_switch(viewer(False, "teacher"))
