import uuid

import pytest
from pydantic import ValidationError

from app.db.schemas.staff_records import StaffDocumentsWrite, StaffHealthWrite


def test_a_document_as_the_school_hub_sends_it() -> None:
    data = StaffDocumentsWrite.model_validate(
        {
            "documents": [
                {
                    "id": str(uuid.uuid4()),
                    "name": "Contract",
                    "category": "Contract",
                    "date": "",
                    "notes": " ",
                }
            ]
        }
    )
    doc = data.documents[0]
    assert doc.date is None and doc.notes is None


def test_health_reads_camel_case_and_leaves_out_what_wasnt_sent() -> None:
    data = StaffHealthWrite.model_validate(
        {"emergencyContact": "Jane", "emergencyPhone": "", "allergies": []}
    )
    assert data.emergency_contact == "Jane" and data.emergency_phone is None
    assert data.model_dump(exclude_unset=True, exclude={"conditions", "allergies"}) == {
        "emergency_contact": "Jane",
        "emergency_phone": None,
    }
    assert data.conditions is None and data.allergies == []


@pytest.mark.parametrize(
    "payload",
    [
        {"allergies": [{"name": "Peanuts", "severity": "Deadly"}]},
        {"conditions": [{"name": ""}]},
        {"notes": "x" * 2001},
        {"conditions": [{"name": "x"}] * 101},
    ],
)
def test_health_refuses_bad_values(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        StaffHealthWrite.model_validate(payload)
