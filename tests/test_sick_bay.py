import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.crud.sick_bay import apply_update, has_alert
from app.db.schemas.sick_bay import VisitCreate, VisitUpdate

NOW = datetime(2026, 10, 30, 9, 15, tzinfo=UTC)
EARLIER = datetime(2026, 10, 30, 8, 50, tzinfo=UTC)


def _visit(status: str = "waiting", **fields: Any) -> Any:
    base = {
        "status": status,
        "temperature": None,
        "treatment": None,
        "nurse_notes": None,
        "arrived_at": None,
        "closed_at": None,
        "closed_by_name": None,
    }
    return SimpleNamespace(**{**base, **fields})


def test_arriving_stamps_the_time() -> None:
    visit = _visit()
    apply_update(visit, {"status": "in"}, now=NOW, by="Nurse Amina")
    assert visit.status == "in"
    assert visit.arrived_at == NOW
    assert visit.closed_at is None


def test_closing_stamps_when_and_who() -> None:
    visit = _visit("in", arrived_at=EARLIER)
    apply_update(visit, {"status": "back", "treatment": "Paracetamol"}, now=NOW, by="Nurse Amina")
    assert (visit.status, visit.arrived_at, visit.closed_at) == ("back", EARLIER, NOW)
    assert visit.closed_by_name == "Nurse Amina"
    assert visit.treatment == "Paracetamol"


def test_closing_straight_from_waiting_counts_as_arrived() -> None:
    visit = _visit()
    apply_update(visit, {"status": "home"}, now=NOW, by=None)
    assert visit.arrived_at == NOW


def test_a_closed_visit_cant_reopen() -> None:
    visit = _visit("back", closed_at=EARLIER)
    with pytest.raises(HTTPException) as caught:
        apply_update(visit, {"status": "in"}, now=NOW, by=None)
    assert caught.value.status_code == 409


def test_a_closed_visit_can_correct_its_outcome() -> None:
    visit = _visit("back", arrived_at=EARLIER, closed_at=EARLIER)
    apply_update(visit, {"status": "hospital"}, now=NOW, by="Nurse Amina")
    assert visit.status == "hospital"


def test_fields_left_out_are_kept_and_blanks_clear() -> None:
    visit = _visit("in", temperature=38.2, treatment="Rest")
    changes = VisitUpdate(treatment="  ").model_dump(exclude_unset=True)
    apply_update(visit, changes, now=NOW, by=None)
    assert visit.temperature == 38.2
    assert visit.treatment is None


def test_temperatures_outside_a_body_range_are_refused() -> None:
    with pytest.raises(ValidationError):
        VisitUpdate(temperature=380)


def test_a_complaint_is_needed() -> None:
    with pytest.raises(ValidationError):
        VisitCreate.model_validate({"studentId": str(uuid.uuid4()), "complaint": ""})


def _profile(**fields: Any) -> Any:
    base: dict[str, Any] = {
        "allergies": [],
        "conditions": [],
        "medications": [],
        "emergency_notes": None,
    }
    return SimpleNamespace(**{**base, **fields})


def test_an_empty_record_has_no_alert() -> None:
    assert not has_alert(_profile())
    assert not has_alert(_profile(emergency_notes="  "))


@pytest.mark.parametrize(
    "fields",
    [
        {"allergies": [SimpleNamespace(name="Peanuts")]},
        {"conditions": [SimpleNamespace(name="Asthma")]},
        {"medications": [SimpleNamespace(is_active=True)]},
        {"emergency_notes": "Inhaler in bag"},
    ],
)
def test_anything_staff_should_know_is_an_alert(fields: dict[str, Any]) -> None:
    assert has_alert(_profile(**fields))


def test_a_medicine_no_longer_taken_isnt_an_alert() -> None:
    assert not has_alert(_profile(medications=[SimpleNamespace(is_active=False)]))
