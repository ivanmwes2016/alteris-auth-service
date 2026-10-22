import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.crud import performance
from app.db.schemas.performance import PerformanceRead, PerformanceUpdate
from app.helpers.pagination import PageParams


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


ADA, ADA_TWIN, BEA = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _record(
    student: str, subject: str, score: str, student_id: uuid.UUID | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        student_id=student_id, student_name=student, subject=subject, score=score
    )


def _student(student_id: uuid.UUID, name: str, school_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=student_id, name=name, school_id=school_id)


def _db() -> Mock:
    db = Mock()
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    return db


def _update(*marks: dict[str, str]) -> PerformanceUpdate:
    return PerformanceUpdate.model_validate(
        {"class": "P1 A", "term": "T1", "year": 2026, "marks": list(marks)}
    )


@pytest.mark.asyncio
async def test_saving_some_students_leaves_everyone_else_alone() -> None:
    kept = _record("Bea", "Maths", "70", BEA)
    cleared = _record("Ada", "English", "60", ADA)
    updated = _record("Ada", "Maths", "50", ADA)
    db = _db()

    with (
        patch.object(performance, "_get_records", AsyncMock(return_value=[kept, cleared, updated])),
        patch.object(
            performance, "_students_by_id", AsyncMock(return_value={ADA: _student(ADA, "Ada")})
        ),
        patch.object(performance, "get_performance", AsyncMock(return_value=PerformanceRead())),
    ):
        await performance.sync_marks(
            db, tenant_id=uuid.uuid4(), data=_update({"student_id": str(ADA), "Maths": "99"})
        )

    assert updated.score == "99"
    deleted = [call.args[0] for call in db.delete.await_args_list]
    assert deleted == [cleared]  # Ada's omitted English goes; Bea wasn't sent, so she keeps hers


@pytest.mark.asyncio
async def test_marks_follow_the_student_id_not_the_name() -> None:
    # Saved before Ada was renamed; a namesake in the class has their own mark.
    ada_maths = _record("Ada Old", "Maths", "50", ADA)
    twin_maths = _record("Ada Old", "Maths", "80", ADA_TWIN)
    db = _db()

    with (
        patch.object(performance, "_get_records", AsyncMock(return_value=[ada_maths, twin_maths])),
        patch.object(
            performance, "_students_by_id", AsyncMock(return_value={ADA: _student(ADA, "Ada New")})
        ),
        patch.object(performance, "get_performance", AsyncMock(return_value=PerformanceRead())),
    ):
        await performance.sync_marks(
            db,
            tenant_id=uuid.uuid4(),
            data=_update({"student_id": str(ADA), "student": "Ada Old", "Maths": "65"}),
        )

    assert (ada_maths.score, ada_maths.student_name) == ("65", "Ada New")
    assert (twin_maths.score, twin_maths.student_name) == ("80", "Ada Old")
    db.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_new_mark_is_saved_against_the_student_id() -> None:
    db = _db()
    with (
        patch.object(performance, "_get_records", AsyncMock(return_value=[])),
        patch.object(
            performance, "_students_by_id", AsyncMock(return_value={BEA: _student(BEA, "Bea")})
        ),
        patch.object(performance, "get_performance", AsyncMock(return_value=PerformanceRead())),
    ):
        await performance.sync_marks(
            db, tenant_id=uuid.uuid4(), data=_update({"student_id": str(BEA), "Art": "77"})
        )

    (added,) = [call.args[0] for call in db.add.call_args_list]
    assert (added.student_id, added.student_name, added.subject, added.score) == (
        BEA,
        "Bea",
        "Art",
        "77",
    )


@pytest.mark.asyncio
async def test_unmatched_older_marks_are_still_saved_by_name() -> None:
    old = _record("Cleo", "Maths", "40")
    db = _db()
    with (
        patch.object(performance, "_get_records", AsyncMock(return_value=[old])),
        patch.object(performance, "_students_by_id", AsyncMock(return_value={})),
        patch.object(performance, "get_performance", AsyncMock(return_value=PerformanceRead())),
    ):
        await performance.sync_marks(
            db, tenant_id=uuid.uuid4(), data=_update({"student": "Cleo", "Maths": "45"})
        )

    assert old.score == "45"


@pytest.mark.asyncio
@pytest.mark.parametrize("student_id", [str(uuid.uuid4()), "not-a-uuid"])
async def test_a_student_from_another_school_is_refused(student_id: str) -> None:
    with (
        patch.object(performance, "_students_by_id", AsyncMock(return_value={})),
        pytest.raises(performance.UnknownStudentError),
    ):
        await performance.sync_marks(
            _db(), tenant_id=uuid.uuid4(), data=_update({"student_id": student_id, "Maths": "1"})
        )


@pytest.mark.asyncio
async def test_ranked_roster_is_keyed_by_student_id() -> None:
    records = [
        _record("Ada Old", "Maths", "90", ADA),  # renamed since: shown by current name
        _record("Ada Old", "Maths", "60", ADA_TWIN),
        _record("Gone", "Maths", "70"),  # never matched to a student
    ]
    roster = [
        _student(ADA, "Ada New", "S001"),
        _student(ADA_TWIN, "Ada Old", "S002"),
        _student(BEA, "Bea", None),  # no marks yet
    ]
    with (
        patch.object(performance, "_get_records", AsyncMock(return_value=records)),
        patch.object(
            performance,
            "_students_by_id",
            AsyncMock(return_value={s.id: s for s in roster[:2]}),
        ),
        patch.object(performance, "_get_trends", AsyncMock(return_value=[])),
        patch.object(performance, "_class_subjects", AsyncMock(return_value=["Maths"])),
        patch.object(performance, "_class_roster", AsyncMock(return_value=roster)),
    ):
        page = await performance.get_performance_page(
            Mock(),
            tenant_id=uuid.uuid4(),
            class_name="P1 A",
            term_label="T1",
            year=2026,
            params=PageParams(page=1, page_size=25, search=None),
        )

    assert [(r.student_id, r.school_id, r.student, r.rank, r.average) for r in page.items] == [
        (ADA, "S001", "Ada New", 1, 90),
        (None, None, "Gone", 2, 70),
        (ADA_TWIN, "S002", "Ada Old", 3, 60),
        (BEA, None, "Bea", 4, 0),
    ]
    assert page.summary.top is not None and page.summary.top.student_id == ADA
