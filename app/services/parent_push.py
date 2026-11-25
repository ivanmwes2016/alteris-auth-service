"""
Pushes to parents about their children (app.crud.parent_access): marked late or
absent on the daily register, sent to the sick bay or home/hospital from it, new
behaviour incidents and recognitions, and term marks published. Each parent can turn each kind off.

Notices are built while the request runs, then sent as a background task with its
own database session, after the response. Sending never raises.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date

from app.core.db import SessionLocal
from app.crud import parent_access
from app.db.models.behaviour import StudentBehaviourProfile
from app.db.models.student import Student
from app.db.schemas.parent_access import Topic
from app.services.push import send_push, tokens_for

log = logging.getLogger(__name__)

PARENTS_CHANNEL = "parents"  # the app's Android channel for these

# Daily-register marks that tell a parent something: absent without a reason given,
# or late. Excused absences are usually the parent's own note, so they aren't sent.
NOTIFY_MARKS = {"Late": "late", "Unexcused": "absent"}


@dataclass(frozen=True)
class ParentNotice:
    student_id: uuid.UUID
    topic: Topic
    title: str
    body: str
    data: dict[str, str] = field(default_factory=dict)


def _data(student_id: uuid.UUID, topic: Topic, screen: str) -> dict[str, str]:
    """What the app opens when the push is tapped."""
    return {"kind": "parent", "topic": topic, "studentId": str(student_id), "screen": screen}


# ---------------------------------------------------------------------------
# Building notices (pure; unit-tested)
# ---------------------------------------------------------------------------
def attendance_notice(
    *,
    student_id: uuid.UUID,
    student_name: str,
    status: str,
    previous: str | None,
    day: date,
    today: date,
) -> ParentNotice | None:
    """A daily-register mark worth a push, or None (unchanged, present, excused)."""
    word = NOTIFY_MARKS.get(status)
    if word is None or status == previous:
        return None
    name = parent_access.first_name(student_name) or student_name
    when = "today" if day == today else f"on {day.strftime('%A')} {day.day} {day:%B}"
    return ParentNotice(
        student_id=student_id,
        topic="attendance",
        title=f"{name} was marked {word}",
        body=f"{name} was marked {word} on the register {when}.",
        data=_data(student_id, "attendance", "attendance"),
    )


def sick_bay_sent_notice(
    *, student_id: uuid.UUID, student_name: str, complaint: str
) -> ParentNotice:
    name = parent_access.first_name(student_name) or student_name
    return ParentNotice(
        student_id=student_id,
        topic="health",
        title=f"{name} is at the sick bay",
        body=f"Sent to the sick bay with: {complaint}",
        data=_data(student_id, "health", "health"),
    )


def sick_bay_closed_notice(
    *, student_id: uuid.UUID, student_name: str, outcome: str, complaint: str
) -> ParentNotice | None:
    """Only going home or to hospital; back to class needs no push."""
    name = parent_access.first_name(student_name) or student_name
    if outcome == "home":
        title = f"{name} is being sent home"
    elif outcome == "hospital":
        title = f"{name} was taken to hospital"
    else:
        return None
    return ParentNotice(
        student_id=student_id,
        topic="health",
        title=title,
        body=f"From the sick bay, after: {complaint}",
        data=_data(student_id, "health", "health"),
    )


def incident_notice(
    *, student_id: uuid.UUID, student_name: str, incident: str, severity: str
) -> ParentNotice:
    name = parent_access.first_name(student_name) or student_name
    return ParentNotice(
        student_id=student_id,
        topic="behaviour",
        title=f"Behaviour report for {name}",
        body=f"{incident} ({severity.lower()})",
        data=_data(student_id, "behaviour", "behaviour"),
    )


def recognition_notice(
    *, student_id: uuid.UUID, student_name: str, recognition: str
) -> ParentNotice:
    name = parent_access.first_name(student_name) or student_name
    return ParentNotice(
        student_id=student_id,
        topic="behaviour",
        title=f"{name} was recognised",
        body=recognition,
        data=_data(student_id, "behaviour", "behaviour"),
    )


def results_notice(
    *, student_id: uuid.UUID, student_name: str, term: str, year: int
) -> ParentNotice:
    name = parent_access.first_name(student_name) or student_name
    return ParentNotice(
        student_id=student_id,
        topic="results",
        title=f"{name}'s {term} {year} results are out",
        body="Open the app to see their marks.",
        data=_data(student_id, "results", "results"),
    )


def new_behaviour_notices(
    student: Student, profile: StudentBehaviourProfile, *, known: set[uuid.UUID]
) -> list[ParentNotice]:
    """One notice per incident or recognition that wasn't on the profile before."""
    notices = [
        incident_notice(
            student_id=student.id,
            student_name=student.name,
            incident=i.name,
            severity=str(getattr(i.severity, "value", i.severity)),
        )
        for i in profile.incidents
        if i.id not in known
    ]
    notices += [
        recognition_notice(student_id=student.id, student_name=student.name, recognition=r.name)
        for r in profile.recognitions
        if r.id not in known
    ]
    return notices


# ---------------------------------------------------------------------------
# Sending (background task)
# ---------------------------------------------------------------------------
async def notify_parents(notices: list[ParentNotice]) -> None:
    if not notices:
        return
    try:
        async with SessionLocal() as db:
            by_topic: dict[Topic, set[uuid.UUID]] = {}
            for notice in notices:
                by_topic.setdefault(notice.topic, set()).add(notice.student_id)
            wanted = {
                topic: await parent_access.recipients(db, student_ids, topic)
                for topic, student_ids in by_topic.items()
            }
            for notice in notices:
                user_ids = wanted[notice.topic].get(notice.student_id)
                if not user_ids:
                    continue
                tokens = await tokens_for(db, list(user_ids))
                await send_push(
                    tokens,
                    title=notice.title,
                    body=notice.body,
                    data=notice.data,
                    channel=PARENTS_CHANNEL,
                )
    except Exception:
        log.exception("could not send %d parent notification(s)", len(notices))
