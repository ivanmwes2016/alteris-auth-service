import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.crud import performance
from app.db.schemas.performance import PerformanceRead, PerformanceUpdate


@pytest.mark.parametrize(("value", "rounded"), [(84.5, 85), (85.5, 86), (84.4, 84), (0.0, 0)])
def test_rounds_half_up_like_the_frontend(value: float, rounded: int) -> None:
    assert performance._round_half_up(value) == rounded


def test_average_counts_missing_and_non_numeric_marks_as_zero() -> None:
    subjects = ["Maths", "English", "Art"]
    assert performance._student_average({"Maths": "90", "English": "abc"}, subjects) == 30
    assert performance._student_average({}, []) == 0


@pytest.mark.parametrize(("name", "stream", "shown"), [("P1", "A", "P1 A"), ("P1", None, "P1")])
def test_class_display_name_matches_the_frontend(name: str, stream: str | None, shown: str) -> None:
    assert performance._display_name(SimpleNamespace(name=name, stream=stream)) == shown  # type: ignore[arg-type]


def _record(student: str, subject: str, score: str) -> SimpleNamespace:
    return SimpleNamespace(student_name=student, subject=subject, score=score)


@pytest.mark.asyncio
async def test_saving_some_students_leaves_everyone_else_alone() -> None:
    kept = _record("Bea", "Maths", "70")
    cleared = _record("Ada", "English", "60")
    updated = _record("Ada", "Maths", "50")
    db = Mock()
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    data = PerformanceUpdate.model_validate(
        {"class": "P1 A", "term": "T1", "year": 2026, "marks": [{"student": "Ada", "Maths": "99"}]}
    )

    with (
        patch.object(performance, "_get_records", AsyncMock(return_value=[kept, cleared, updated])),
        patch.object(performance, "get_performance", AsyncMock(return_value=PerformanceRead())),
    ):
        await performance.sync_marks(db, tenant_id=uuid.uuid4(), data=data)

    assert updated.score == "99"
    deleted = [call.args[0] for call in db.delete.await_args_list]
    assert deleted == [cleared]  # Ada's omitted English goes; Bea wasn't sent, so she keeps hers
