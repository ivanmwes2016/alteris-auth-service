"""
Service layer for lesson planning: schemes of work, lesson plans and the
record of work.

Who: everyone who takes registers (Teachers, Head Teachers, Admins and the
Owner) writes schemes and plans for any class; approvers (Owner, Head Teacher,
Admin) also approve schemes. Other roles can't see planning at all.

A scheme is for one subject and one class name, shared by its streams, in
one term, split into entries by term week (counted from the term's first day).
It is a draft until submitted; an approver approves it or sends it back.
Approval never blocks teaching, and changing an approved scheme submits it
again, showing what changed.

A lesson plan serves one or more lessons of the same subject and class name
in one term week. Its id comes from the phone, so a plan written offline is
saved once however often it's sent; the latest save wins, judged by the
phone's clock within the 72-hour grace window, and earlier versions are kept
(docs/adr/0001 in the marking app repo). A plan first written after its
lesson started is late. Each lesson then gets its own record of work.
"""

import logging
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from supabase import Client

from app.core.config import get_settings
from app.crud.attendance import (
    CLOCK_SKEW,
    GRACE,
    TermSpan,
    Viewer,
    approver_user_ids,
    invalid,
    require_approver,
    resolve,
    staff_name,
    term_on,
    user_names,
)
from app.db.models.academics import SchoolClass, Subject
from app.db.models.planning import (
    LessonPlan,
    LessonPlanVersion,
    PlannedLesson,
    PlanningAttachment,
    PlanningComment,
    SchemeEntry,
    SchemeOfWork,
)
from app.db.models.staff import Staff
from app.db.schemas.attendance import PersonRead, RegisterKey
from app.db.schemas.planning import (
    AttachmentIn,
    AttachmentRead,
    CommentIn,
    CommentRead,
    EntriesWrite,
    EntryIn,
    EntryRead,
    LessonKey,
    PlannedLessonRead,
    PlanRead,
    PlanSaveResult,
    PlanSummary,
    PlanVersionRead,
    PlanWrite,
    RecordRead,
    RecordWrite,
    SchemeCreate,
    SchemeRead,
    SchemeSummary,
    StreamProgress,
    TermWeekRead,
    UploadUrlRead,
    UploadUrlRequest,
)
from app.services.push import PLANNING_CHANNEL, send_push, tokens_for

log = logging.getLogger(__name__)

ATTACHMENT_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "application/pdf": "pdf",
}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # matches the bucket's limit
MAX_ATTACHMENTS = 10
SIGNED_URL_SECONDS = 60 * 60

# The fields copied into a scheme's approval snapshot and compared against it.
ENTRY_FIELDS = (
    "week",
    "topic",
    "subtopic",
    "objectives",
    "methods",
    "materials",
    "references",
    "lessons",
)
PLAN_FIELDS = (
    "topic",
    "subtopic",
    "objectives",
    "materials",
    "introduction",
    "development",
    "assessment",
    "conclusion",
)


# ---------------------------------------------------------------------------
# Pure rules (unit-tested without a database)
# ---------------------------------------------------------------------------
def week_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def term_week(day: date, term: TermSpan) -> int:
    """Week 1 is the week (Monday to Sunday) the term starts in."""
    return (week_monday(day) - week_monday(term.starts_on)).days // 7 + 1


def term_week_count(term: TermSpan) -> int:
    return term_week(term.ends_on, term)


def week_span(term: TermSpan, week: int) -> tuple[date, date]:
    """A term week's first and last day, kept inside the term."""
    monday = week_monday(term.starts_on) + timedelta(weeks=week - 1)
    return max(monday, term.starts_on), min(monday + timedelta(days=6), term.ends_on)


def trusted_time(device: datetime, now: datetime) -> datetime:
    """
    The phone's time of a save, if it's believable: not in the future and
    within the grace window. Otherwise the time it reached the server.
    """
    if device > now + CLOCK_SKEW or device < now - GRACE:
        return now
    return device


def is_late(written_at: datetime, first_lesson_starts: datetime) -> bool:
    return written_at > first_lesson_starts


def status_after_edit(scheme_status: str) -> str:
    """Changing an approved scheme submits it again; other stages stay as they are."""
    return "submitted" if scheme_status == "approved" else scheme_status


def can_submit(scheme_status: str) -> bool:
    return scheme_status in ("draft", "sent_back")


def entry_snapshot(entry: SchemeEntry | EntryIn) -> dict[str, Any]:
    return {"id": str(entry.id) if entry.id else None} | {
        name: getattr(entry, name) for name in ENTRY_FIELDS
    }


def changes_since_approval(
    snapshot: Sequence[dict[str, Any]] | None, entries: Iterable[dict[str, Any]]
) -> tuple[set[str], list[str]]:
    """
    Entry ids added or changed since approval, and the entries removed since
    (as "Week 3: Cell division"). Nothing has changed for a never-approved scheme.
    """
    if snapshot is None:
        return set(), []
    before = {e["id"]: e for e in snapshot}
    now = {e["id"]: e for e in entries}
    changed = {i for i, e in now.items() if before.get(i) != e}
    removed = [f"Week {e['week']}: {e['topic']}" for i, e in before.items() if i not in now]
    return changed, removed


@dataclass(frozen=True)
class EntryWeek:
    id: uuid.UUID
    week: int
    lessons: int


def stream_progress(
    entries: Sequence[EntryWeek], covered: set[uuid.UUID], current_week: int | None
) -> tuple[int, int, int]:
    """
    (covered through week, weeks behind, lessons the scheme planned so far).
    An entry is covered once one of its lessons is marked Taught; a week with
    no entries is covered. Only finished weeks count: during week 9 a stream
    that has covered week 8 is on time.
    """
    last_week = max((e.week for e in entries), default=0)
    through = 0
    for week in range(1, last_week + 1):
        if all(e.id in covered for e in entries if e.week == week):
            through = week
        else:
            break
    if current_week is None:
        return through, 0, 0
    finished = current_week - 1
    so_far = sum(e.lessons for e in entries if e.week <= finished)
    return through, max(0, min(finished, last_week) - through), so_far


def lesson_minutes(starts: time | None, ends: time | None) -> int | None:
    if starts is None or ends is None:
        return None
    return (ends.hour * 60 + ends.minute) - (starts.hour * 60 + starts.minute)


# ---------------------------------------------------------------------------
# Errors and lookups
# ---------------------------------------------------------------------------
def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found")


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def term_span(viewer: Viewer, term: str, year: int) -> TermSpan | None:
    return next((t for t in viewer.terms if t.label == term and t.year == year), None)


async def _subject(db: AsyncSession, viewer: Viewer, subject_id: uuid.UUID) -> Subject:
    subject = await db.scalar(
        select(Subject).where(Subject.id == subject_id, Subject.tenant_id == viewer.tenant_id)
    )
    if subject is None:
        raise _not_found("Subject")
    return subject


async def _streams(db: AsyncSession, viewer: Viewer, class_name: str) -> list[str | None]:
    rows = await db.scalars(
        select(SchoolClass.stream).where(
            SchoolClass.tenant_id == viewer.tenant_id, SchoolClass.name == class_name
        )
    )
    streams = sorted(set(rows), key=lambda s: s or "")
    if not streams:
        raise _not_found("Class")
    return streams


async def _scheme(db: AsyncSession, viewer: Viewer, scheme_id: uuid.UUID) -> SchemeOfWork:
    scheme = await db.scalar(
        select(SchemeOfWork).where(
            SchemeOfWork.id == scheme_id, SchemeOfWork.tenant_id == viewer.tenant_id
        )
    )
    if scheme is None:
        raise _not_found("Scheme of work")
    return scheme


async def _plan(db: AsyncSession, viewer: Viewer, plan_id: uuid.UUID) -> LessonPlan:
    plan = await db.scalar(
        select(LessonPlan).where(LessonPlan.id == plan_id, LessonPlan.tenant_id == viewer.tenant_id)
    )
    if plan is None:
        raise _not_found("Lesson plan")
    return plan


def _scheme_title(scheme: SchemeOfWork) -> str:
    return f"{scheme.class_name} {scheme.subject_name}, {scheme.term} {scheme.year}"


def _person(user_id: uuid.UUID | None, names: dict[uuid.UUID, str | None]) -> PersonRead | None:
    return PersonRead(id=user_id, name=names.get(user_id)) if user_id else None


async def _notify(
    background: BackgroundTasks,
    db: AsyncSession,
    user_ids: Iterable[uuid.UUID | None],
    *,
    skip: uuid.UUID,
    title: str,
    body: str,
    data: dict[str, str],
) -> None:
    ids = sorted({i for i in user_ids if i is not None and i != skip})
    tokens = await tokens_for(db, ids)
    background.add_task(
        send_push, tokens, title=title, body=body, data=data, channel=PLANNING_CHANNEL
    )


# ---------------------------------------------------------------------------
# Attachments: files go straight to private storage on a signed URL
# ---------------------------------------------------------------------------
def _bucket() -> str:
    return get_settings().PLANNING_FILES_BUCKET


async def upload_url(supabase: Client, viewer: Viewer, payload: UploadUrlRequest) -> UploadUrlRead:
    ext = ATTACHMENT_TYPES.get(payload.content_type)
    if ext is None:
        raise invalid("Attach a photo (JPEG, PNG, WebP or HEIC) or a PDF")
    if payload.size > MAX_ATTACHMENT_BYTES:
        raise invalid("Files can be up to 10 MB")
    path = f"{viewer.tenant_id}/{uuid.uuid4()}.{ext}"
    try:
        signed = await run_in_threadpool(
            supabase.storage.from_(_bucket()).create_signed_upload_url, path
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Could not create upload URL") from exc
    return UploadUrlRead(path=path, token=signed["token"])


async def attachment_reads(
    supabase: Client, attachments: Sequence[PlanningAttachment]
) -> list[AttachmentRead]:
    if not attachments:
        return []
    urls: dict[str, str] = {}
    try:
        signed = await run_in_threadpool(
            supabase.storage.from_(_bucket()).create_signed_urls,
            [a.storage_path for a in attachments],
            SIGNED_URL_SECONDS,
        )
        urls = {s["path"]: s["signedURL"] for s in signed if s.get("signedURL")}
    except Exception:
        # Shown without a link rather than failing the whole read.
        log.warning("could not sign %d planning file URL(s)", len(attachments), exc_info=True)
    return [
        AttachmentRead(
            id=a.id,
            file_name=a.file_name,
            content_type=a.content_type,
            size=a.size,
            url=urls.get(a.storage_path),
        )
        for a in attachments
    ]


async def _attachments_for(
    db: AsyncSession, *, entry_ids: Sequence[uuid.UUID] = (), plan_id: uuid.UUID | None = None
) -> list[PlanningAttachment]:
    query = select(PlanningAttachment).order_by(PlanningAttachment.created_at)
    if plan_id is not None:
        query = query.where(PlanningAttachment.plan_id == plan_id)
    elif entry_ids:
        query = query.where(PlanningAttachment.scheme_entry_id.in_(entry_ids))
    else:
        return []
    return list(await db.scalars(query))


async def add_attachment(
    db: AsyncSession,
    viewer: Viewer,
    payload: AttachmentIn,
    *,
    entry_id: uuid.UUID | None = None,
    plan_id: uuid.UUID | None = None,
) -> PlanningAttachment:
    # Only paths this school's upload URLs handed out.
    if not payload.path.startswith(f"{viewer.tenant_id}/") or ".." in payload.path:
        raise invalid("That file wasn't uploaded for your school")
    if payload.content_type not in ATTACHMENT_TYPES or payload.size > MAX_ATTACHMENT_BYTES:
        raise invalid("Attach a photo or a PDF of up to 10 MB")
    existing = await _attachments_for(db, entry_ids=[entry_id] if entry_id else [], plan_id=plan_id)
    if len(existing) >= MAX_ATTACHMENTS:
        raise invalid(f"Up to {MAX_ATTACHMENTS} files each")
    attachment = PlanningAttachment(
        tenant_id=viewer.tenant_id,
        scheme_entry_id=entry_id,
        plan_id=plan_id,
        storage_path=payload.path,
        file_name=payload.file_name.strip(),
        content_type=payload.content_type,
        size=payload.size,
        uploaded_by=viewer.user_id,
    )
    db.add(attachment)
    await db.commit()
    return attachment


async def attach_to_entry(
    db: AsyncSession,
    supabase: Client,
    viewer: Viewer,
    scheme_id: uuid.UUID,
    entry_id: uuid.UUID,
    payload: AttachmentIn,
) -> AttachmentRead:
    scheme = await _scheme(db, viewer, scheme_id)
    if all(e.id != entry_id for e in scheme.entries):
        raise _not_found("Scheme entry")
    attachment = await add_attachment(db, viewer, payload, entry_id=entry_id)
    return (await attachment_reads(supabase, [attachment]))[0]


async def attach_to_plan(
    db: AsyncSession, supabase: Client, viewer: Viewer, plan_id: uuid.UUID, payload: AttachmentIn
) -> AttachmentRead:
    await _plan(db, viewer, plan_id)
    attachment = await add_attachment(db, viewer, payload, plan_id=plan_id)
    return (await attachment_reads(supabase, [attachment]))[0]


async def remove_attachment(
    db: AsyncSession, supabase: Client, viewer: Viewer, attachment_id: uuid.UUID
) -> None:
    attachment = await db.scalar(
        select(PlanningAttachment).where(
            PlanningAttachment.id == attachment_id,
            PlanningAttachment.tenant_id == viewer.tenant_id,
        )
    )
    if attachment is None:
        raise _not_found("File")
    path = attachment.storage_path
    await db.delete(attachment)
    await db.commit()
    try:
        await run_in_threadpool(supabase.storage.from_(_bucket()).remove, [path])
    except Exception:
        # The row is gone; a leftover file is harmless.
        log.warning("could not remove planning file %s", path, exc_info=True)


# ---------------------------------------------------------------------------
# Schemes of work
# ---------------------------------------------------------------------------
def _summary(scheme: SchemeOfWork) -> SchemeSummary:
    changed, removed = changes_since_approval(
        scheme.approved_entries, (entry_snapshot(e) for e in scheme.entries)
    )
    return SchemeSummary(
        id=scheme.id,
        class_name=scheme.class_name,
        subject_id=scheme.subject_id,
        subject_name=scheme.subject_name,
        term=scheme.term,
        year=scheme.year,
        status=scheme.status,
        entry_count=len(scheme.entries),
        changed_since_approval=bool(changed or removed),
        updated_at=scheme.updated_at,
    )


async def list_schemes(
    db: AsyncSession,
    viewer: Viewer,
    *,
    term: str | None,
    year: int | None,
    class_name: str | None,
    subject_id: uuid.UUID | None,
    scheme_status: str | None,
) -> list[SchemeSummary]:
    query = select(SchemeOfWork).where(SchemeOfWork.tenant_id == viewer.tenant_id)
    if term is not None:
        query = query.where(SchemeOfWork.term == term)
    if year is not None:
        query = query.where(SchemeOfWork.year == year)
    if class_name is not None:
        query = query.where(SchemeOfWork.class_name == class_name)
    if subject_id is not None:
        query = query.where(SchemeOfWork.subject_id == subject_id)
    if scheme_status is not None:
        query = query.where(SchemeOfWork.status == scheme_status)
    query = query.order_by(
        SchemeOfWork.year.desc(),
        SchemeOfWork.term,
        SchemeOfWork.class_name,
        SchemeOfWork.subject_name,
    )
    return [_summary(s) for s in await db.scalars(query)]


async def progress_for(
    db: AsyncSession, viewer: Viewer, scheme: SchemeOfWork, current_week: int | None
) -> list[StreamProgress]:
    """Each stream's place in the scheme, from the records of lessons planned against it."""
    streams = await _streams(db, viewer, scheme.class_name)
    rows = (
        await db.execute(
            select(PlannedLesson.stream, PlannedLesson.record_status, LessonPlan.scheme_entry_id)
            .join(LessonPlan, LessonPlan.id == PlannedLesson.plan_id)
            .where(
                LessonPlan.tenant_id == viewer.tenant_id,
                LessonPlan.class_name == scheme.class_name,
                LessonPlan.subject_id == scheme.subject_id,
                LessonPlan.term == scheme.term,
                LessonPlan.year == scheme.year,
            )
        )
    ).all()
    entries = [EntryWeek(e.id, e.week, e.lessons) for e in scheme.entries]
    result = []
    for stream in streams:
        mine = [r for r in rows if r.stream == stream]
        covered = {
            r.scheme_entry_id for r in mine if r.record_status == "taught" and r.scheme_entry_id
        }
        taught = sum(1 for r in mine if r.record_status in ("taught", "partly_taught"))
        through, behind, so_far = stream_progress(entries, covered, current_week)
        result.append(
            StreamProgress(
                stream=stream,
                covered_through_week=through,
                weeks_behind=behind,
                lessons_taught=taught,
                lessons_planned_so_far=so_far,
            )
        )
    return result


async def scheme_read(
    db: AsyncSession, supabase: Client, viewer: Viewer, scheme: SchemeOfWork
) -> SchemeRead:
    span = term_span(viewer, scheme.term, scheme.year)
    current = (
        term_week(viewer.today, span)
        if span is not None and span.starts_on <= viewer.today <= span.ends_on
        else None
    )
    if span is not None:
        count = max(term_week_count(span), max((e.week for e in scheme.entries), default=0))
        weeks = [
            TermWeekRead(
                week=w,
                starts_on=week_span(span, w)[0] if w <= term_week_count(span) else None,
                ends_on=week_span(span, w)[1] if w <= term_week_count(span) else None,
            )
            for w in range(1, count + 1)
        ]
    else:
        count = max((e.week for e in scheme.entries), default=0)
        weeks = [TermWeekRead(week=w, starts_on=None, ends_on=None) for w in range(1, count + 1)]

    changed, removed = changes_since_approval(
        scheme.approved_entries, (entry_snapshot(e) for e in scheme.entries)
    )
    files = await _attachments_for(db, entry_ids=[e.id for e in scheme.entries])
    reads = await attachment_reads(supabase, files)
    by_entry: dict[uuid.UUID, list[AttachmentRead]] = {}
    for f, read in zip(files, reads, strict=True):
        if f.scheme_entry_id:
            by_entry.setdefault(f.scheme_entry_id, []).append(read)
    names = await user_names(db, (scheme.submitted_by, scheme.decided_by))
    summary = _summary(scheme)
    return SchemeRead(
        **summary.model_dump(),
        submitted_by=_person(scheme.submitted_by, names),
        submitted_at=scheme.submitted_at,
        decided_by=_person(scheme.decided_by, names),
        decided_at=scheme.decided_at,
        approved_at=scheme.approved_at,
        entries=[
            EntryRead(
                id=e.id,
                week=e.week,
                position=e.position,
                topic=e.topic,
                subtopic=e.subtopic,
                objectives=e.objectives,
                methods=e.methods,
                materials=e.materials,
                references=e.references,
                lessons=e.lessons,
                attachments=by_entry.get(e.id, []),
                changed_since_approval=str(e.id) in changed,
            )
            for e in scheme.entries
        ],
        removed_since_approval=removed,
        weeks=weeks,
        current_week=current,
        needs_term_dates=span is None,
        progress=await progress_for(db, viewer, scheme, current),
    )


async def get_scheme(
    db: AsyncSession, supabase: Client, viewer: Viewer, scheme_id: uuid.UUID
) -> SchemeRead:
    return await scheme_read(db, supabase, viewer, await _scheme(db, viewer, scheme_id))


def _entry_from(entry: SchemeEntry, data: EntryIn, viewer: Viewer) -> None:
    entry.week = data.week
    entry.position = data.position
    entry.topic = data.topic.strip()
    entry.subtopic = _clean(data.subtopic)
    entry.objectives = _clean(data.objectives)
    entry.methods = _clean(data.methods)
    entry.materials = _clean(data.materials)
    entry.references = _clean(data.references)
    entry.lessons = data.lessons
    entry.updated_by = viewer.user_id


async def create_scheme(
    db: AsyncSession, supabase: Client, viewer: Viewer, payload: SchemeCreate
) -> SchemeRead:
    class_name = payload.class_name.strip()
    await _streams(db, viewer, class_name)
    subject = await _subject(db, viewer, payload.subject_id)
    exists = await db.scalar(
        select(SchemeOfWork.id).where(
            SchemeOfWork.tenant_id == viewer.tenant_id,
            SchemeOfWork.class_name == class_name,
            SchemeOfWork.subject_id == subject.id,
            SchemeOfWork.term == payload.term,
            SchemeOfWork.year == payload.year,
        )
    )
    if exists is not None:
        raise _conflict(
            f"{class_name} {subject.name} already has a scheme for {payload.term} {payload.year}"
        )

    source = None
    if payload.copy_from_id is not None:
        source = await _scheme(db, viewer, payload.copy_from_id)
        if source.class_name != class_name or source.subject_id != subject.id:
            raise invalid("Copy a scheme for the same class and subject")

    scheme = SchemeOfWork(
        tenant_id=viewer.tenant_id,
        class_name=class_name,
        subject_id=subject.id,
        subject_name=subject.name,
        term=payload.term.strip(),
        year=payload.year,
        status="draft",
        copied_from_id=source.id if source else None,
        created_by=viewer.user_id,
    )
    if source is not None:
        scheme.entries = [
            SchemeEntry(
                tenant_id=viewer.tenant_id,
                week=e.week,
                position=e.position,
                topic=e.topic,
                subtopic=e.subtopic,
                objectives=e.objectives,
                methods=e.methods,
                materials=e.materials,
                references=e.references,
                lessons=e.lessons,
                updated_by=viewer.user_id,
            )
            for e in source.entries
        ]
    db.add(scheme)
    await db.commit()
    await db.refresh(scheme)
    return await scheme_read(db, supabase, viewer, scheme)


async def write_entries(
    db: AsyncSession, supabase: Client, viewer: Viewer, scheme_id: uuid.UUID, payload: EntriesWrite
) -> SchemeRead:
    """Every entry at once (the School Hub's table): ids update, no id adds, left out removes."""
    scheme = await _scheme(db, viewer, scheme_id)
    current = {e.id: e for e in scheme.entries}
    kept: list[SchemeEntry] = []
    for data in payload.entries:
        if data.id is not None:
            entry = current.get(data.id)
            if entry is None:
                raise _not_found("Scheme entry")
        else:
            entry = SchemeEntry(tenant_id=viewer.tenant_id, scheme_id=scheme.id)
        _entry_from(entry, data, viewer)
        kept.append(entry)
    scheme.entries = kept
    scheme.status = status_after_edit(scheme.status)
    await db.commit()
    await db.refresh(scheme)
    return await scheme_read(db, supabase, viewer, scheme)


async def write_entry(
    db: AsyncSession,
    supabase: Client,
    viewer: Viewer,
    scheme_id: uuid.UUID,
    entry_id: uuid.UUID,
    payload: EntryIn,
) -> SchemeRead:
    """One entry (the app, in week 6: "this needs another week")."""
    scheme = await _scheme(db, viewer, scheme_id)
    entry = next((e for e in scheme.entries if e.id == entry_id), None)
    if entry is None:
        raise _not_found("Scheme entry")
    _entry_from(entry, payload, viewer)
    scheme.status = status_after_edit(scheme.status)
    await db.commit()
    await db.refresh(scheme)
    return await scheme_read(db, supabase, viewer, scheme)


async def delete_scheme(db: AsyncSession, viewer: Viewer, scheme_id: uuid.UUID) -> None:
    """Approvers, or its author while it's still a draft."""
    scheme = await _scheme(db, viewer, scheme_id)
    if not viewer.approver and not (
        scheme.created_by == viewer.user_id and scheme.status == "draft"
    ):
        raise _forbidden("Only an approver can delete a scheme that has been submitted")
    await db.delete(scheme)
    await db.commit()


async def submit_scheme(
    db: AsyncSession,
    supabase: Client,
    viewer: Viewer,
    scheme_id: uuid.UUID,
    background: BackgroundTasks,
) -> SchemeRead:
    scheme = await _scheme(db, viewer, scheme_id)
    if not can_submit(scheme.status):
        raise _conflict("This scheme is already submitted")
    if not scheme.entries:
        raise invalid("Add at least one entry before submitting")
    scheme.status = "submitted"
    scheme.submitted_by = viewer.user_id
    scheme.submitted_at = viewer.now
    await db.commit()
    await db.refresh(scheme)
    await _notify(
        background,
        db,
        await approver_user_ids(db, viewer.tenant_id),
        skip=viewer.user_id,
        title="Scheme of work to review",
        body=f"{viewer.user_name or 'A teacher'} submitted {_scheme_title(scheme)}.",
        data={"schemeId": str(scheme.id)},
    )
    return await scheme_read(db, supabase, viewer, scheme)


async def approve_scheme(
    db: AsyncSession,
    supabase: Client,
    viewer: Viewer,
    scheme_id: uuid.UUID,
    background: BackgroundTasks,
) -> SchemeRead:
    require_approver(viewer)
    scheme = await _scheme(db, viewer, scheme_id)
    if scheme.status != "submitted":
        raise _conflict("Only a submitted scheme can be approved")
    scheme.status = "approved"
    scheme.decided_by = viewer.user_id
    scheme.decided_at = viewer.now
    scheme.approved_at = viewer.now
    scheme.approved_entries = [entry_snapshot(e) for e in scheme.entries]
    await db.commit()
    await db.refresh(scheme)
    await _notify(
        background,
        db,
        (scheme.submitted_by, scheme.created_by),
        skip=viewer.user_id,
        title="Scheme of work approved",
        body=f"{_scheme_title(scheme)} was approved.",
        data={"schemeId": str(scheme.id)},
    )
    return await scheme_read(db, supabase, viewer, scheme)


async def send_back_scheme(
    db: AsyncSession,
    supabase: Client,
    viewer: Viewer,
    scheme_id: uuid.UUID,
    comment: str,
    background: BackgroundTasks,
) -> SchemeRead:
    require_approver(viewer)
    scheme = await _scheme(db, viewer, scheme_id)
    if scheme.status != "submitted":
        raise _conflict("Only a submitted scheme can be sent back")
    scheme.status = "sent_back"
    scheme.decided_by = viewer.user_id
    scheme.decided_at = viewer.now
    db.add(
        PlanningComment(
            tenant_id=viewer.tenant_id,
            scheme_id=scheme.id,
            author_id=viewer.user_id,
            by_approver=True,
            sent_back=True,
            body=comment.strip(),
        )
    )
    await db.commit()
    await db.refresh(scheme)
    await _notify(
        background,
        db,
        (scheme.submitted_by, scheme.created_by),
        skip=viewer.user_id,
        title="Scheme of work sent back",
        body=f"{scheme.class_name} {scheme.subject_name}: {comment.strip()}",
        data={"schemeId": str(scheme.id)},
    )
    return await scheme_read(db, supabase, viewer, scheme)


# ---------------------------------------------------------------------------
# Comments: one thread per scheme and per lesson plan
# ---------------------------------------------------------------------------
async def _comment_reads(
    db: AsyncSession, comments: Sequence[PlanningComment]
) -> list[CommentRead]:
    names = await user_names(db, (c.author_id for c in comments))
    return [
        CommentRead(
            id=c.id,
            author=PersonRead(id=c.author_id, name=names.get(c.author_id) if c.author_id else None),
            by_approver=c.by_approver,
            sent_back=c.sent_back,
            body=c.body,
            created_at=c.created_at,
        )
        for c in comments
    ]


async def list_comments(
    db: AsyncSession,
    viewer: Viewer,
    *,
    scheme_id: uuid.UUID | None = None,
    plan_id: uuid.UUID | None = None,
) -> list[CommentRead]:
    if scheme_id is not None:
        await _scheme(db, viewer, scheme_id)
        where = PlanningComment.scheme_id == scheme_id
    else:
        assert plan_id is not None
        await _plan(db, viewer, plan_id)
        where = PlanningComment.plan_id == plan_id
    comments = list(
        await db.scalars(select(PlanningComment).where(where).order_by(PlanningComment.created_at))
    )
    return await _comment_reads(db, comments)


async def add_comment(
    db: AsyncSession,
    viewer: Viewer,
    payload: CommentIn,
    background: BackgroundTasks,
    *,
    scheme_id: uuid.UUID | None = None,
    plan_id: uuid.UUID | None = None,
) -> CommentRead:
    """The author hears about it, and so does everyone already in the thread."""
    if scheme_id is not None:
        scheme = await _scheme(db, viewer, scheme_id)
        authors: list[uuid.UUID | None] = [scheme.created_by, scheme.submitted_by]
        title = f"{scheme.class_name} {scheme.subject_name} scheme"
        data = {"schemeId": str(scheme.id)}
        where = PlanningComment.scheme_id == scheme.id
    else:
        assert plan_id is not None
        plan = await _plan(db, viewer, plan_id)
        authors = [plan.created_by]
        title = f"{plan.class_name} {plan.subject_name}: {plan.topic}"
        data = {"planId": str(plan.id)}
        where = PlanningComment.plan_id == plan.id
    earlier = list(await db.scalars(select(PlanningComment.author_id).where(where)))
    comment = PlanningComment(
        tenant_id=viewer.tenant_id,
        scheme_id=scheme_id,
        plan_id=plan_id,
        author_id=viewer.user_id,
        by_approver=viewer.approver,
        body=payload.body.strip(),
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    await _notify(
        background,
        db,
        [*authors, *earlier],
        skip=viewer.user_id,
        title=title,
        body=f"{viewer.user_name or 'Someone'}: {comment.body}",
        data=data,
    )
    return (await _comment_reads(db, [comment]))[0]


# ---------------------------------------------------------------------------
# Lessons
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LessonSpec:
    """A lesson someone can plan or record: timetabled on its date, or extra."""

    date: date
    lesson_id: uuid.UUID | None
    class_name: str
    stream: str | None
    subject_id: uuid.UUID | None
    subject_name: str
    starts_at: time
    ends_at: time | None
    minutes: int
    staff_id: uuid.UUID | None
    teacher_name: str | None

    def key(self) -> LessonKey:
        if self.lesson_id is not None:
            return LessonKey(date=self.date, lesson_id=self.lesson_id)
        return LessonKey(
            date=self.date,
            class_name=self.class_name,
            stream=self.stream,
            subject_id=self.subject_id,
            starts_at=self.starts_at,
            minutes=self.minutes,
        )


async def resolve_lesson(db: AsyncSession, viewer: Viewer, key: LessonKey) -> LessonSpec:
    """Checks the lesson against the school and its timetable, as registers do."""
    if key.lesson_id is not None:
        spec = await resolve(
            db, viewer, RegisterKey(kind="lesson", date=key.date, lesson_id=key.lesson_id)
        )
    else:
        spec = await resolve(
            db,
            viewer,
            RegisterKey(
                kind="lesson",
                date=key.date,
                class_name=key.class_name,
                stream=key.stream,
                subject_id=key.subject_id,
                starts_at=key.starts_at,
            ),
        )
    assert spec.starts_at is not None or key.lesson_id is not None
    if spec.starts_at is None:
        raise invalid("That lesson has no start time on the timetable")
    minutes = lesson_minutes(spec.starts_at, spec.ends_at) if key.lesson_id else key.minutes
    return LessonSpec(
        date=spec.date,
        lesson_id=spec.lesson_id,
        class_name=spec.class_name,
        stream=spec.stream,
        subject_id=spec.subject_id,
        subject_name=spec.subject_name or "",
        starts_at=spec.starts_at,
        ends_at=spec.ends_at,
        minutes=minutes or 0,
        staff_id=spec.staff_id,
        teacher_name=spec.teacher_name,
    )


async def _planned_lesson(
    db: AsyncSession, viewer: Viewer, spec: LessonSpec
) -> PlannedLesson | None:
    query = select(PlannedLesson).where(
        PlannedLesson.tenant_id == viewer.tenant_id, PlannedLesson.date == spec.date
    )
    if spec.lesson_id is not None:
        query = query.where(PlannedLesson.lesson_id == spec.lesson_id)
    else:
        query = query.where(
            PlannedLesson.lesson_id.is_(None),
            PlannedLesson.class_name == spec.class_name,
            PlannedLesson.stream.is_(None)
            if spec.stream is None
            else PlannedLesson.stream == spec.stream,
            PlannedLesson.subject_id == spec.subject_id,
            PlannedLesson.starts_at == spec.starts_at,
        )
    found: PlannedLesson | None = await db.scalar(query)
    return found


def _new_planned_lesson(viewer: Viewer, spec: LessonSpec) -> PlannedLesson:
    return PlannedLesson(
        tenant_id=viewer.tenant_id,
        date=spec.date,
        lesson_id=spec.lesson_id,
        class_name=spec.class_name,
        stream=spec.stream,
        subject_id=spec.subject_id,
        subject_name=spec.subject_name,
        starts_at=spec.starts_at,
        minutes=spec.minutes,
        staff_id=spec.staff_id if spec.lesson_id else viewer.staff_id,
        created_by=viewer.user_id,
    )


def planned_key(row: PlannedLesson) -> LessonKey:
    if row.lesson_id is not None:
        return LessonKey(date=row.date, lesson_id=row.lesson_id)
    return LessonKey(
        date=row.date,
        class_name=row.class_name,
        stream=row.stream,
        subject_id=row.subject_id,
        starts_at=row.starts_at,
        minutes=row.minutes,
    )


def record_read(row: PlannedLesson, names: dict[uuid.UUID, str | None]) -> RecordRead | None:
    if row.record_status is None or row.recorded_at is None:
        return None
    return RecordRead(
        status=row.record_status,
        reason=row.not_taught_reason,
        comment=row.record_comment,
        reflection=row.reflection,
        recorded_by=_person(row.recorded_by, names),
        recorded_at=row.recorded_at,
    )


async def _staff_names(db: AsyncSession, viewer: Viewer) -> dict[uuid.UUID, str | None]:
    return {
        s.id: staff_name(s)
        for s in await db.scalars(select(Staff).where(Staff.tenant_id == viewer.tenant_id))
    }


# ---------------------------------------------------------------------------
# Lesson plans
# ---------------------------------------------------------------------------
async def _plan_summary(
    db: AsyncSession,
    viewer: Viewer,
    plan: LessonPlan,
    staff: dict[uuid.UUID, str | None] | None = None,
) -> PlanSummary:
    staff = staff if staff is not None else await _staff_names(db, viewer)
    names = await user_names(db, [plan.created_by, *(r.recorded_by for r in plan.lessons)])
    return PlanSummary(
        id=plan.id,
        class_name=plan.class_name,
        subject_id=plan.subject_id,
        subject_name=plan.subject_name,
        term=plan.term,
        year=plan.year,
        week=plan.week,
        topic=plan.topic,
        subtopic=plan.subtopic,
        late=plan.late,
        written_at=plan.written_at,
        written_by=_person(plan.created_by, names),
        lessons=[
            PlannedLessonRead(
                id=r.id,
                key=planned_key(r),
                date=r.date,
                class_name=r.class_name,
                stream=r.stream,
                subject_name=r.subject_name,
                starts_at=r.starts_at,
                minutes=r.minutes,
                teacher=PersonRead(id=r.staff_id, name=staff.get(r.staff_id))
                if r.staff_id
                else None,
                record=record_read(r, names),
            )
            for r in plan.lessons
        ],
    )


async def plan_read(
    db: AsyncSession, supabase: Client, viewer: Viewer, plan: LessonPlan
) -> PlanRead:
    summary = await _plan_summary(db, viewer, plan)
    names = await user_names(db, [plan.updated_by])
    versions = await db.scalars(
        select(LessonPlanVersion.id).where(LessonPlanVersion.plan_id == plan.id)
    )
    return PlanRead(
        **summary.model_dump(),
        scheme_entry_id=plan.scheme_entry_id,
        objectives=plan.objectives,
        materials=plan.materials,
        introduction=plan.introduction,
        development=plan.development,
        assessment=plan.assessment,
        conclusion=plan.conclusion,
        saved_at=plan.saved_at,
        updated_by=_person(plan.updated_by, names),
        attachments=await attachment_reads(supabase, await _attachments_for(db, plan_id=plan.id)),
        versions=len(list(versions)),
    )


async def get_plan(
    db: AsyncSession, supabase: Client, viewer: Viewer, plan_id: uuid.UUID
) -> PlanRead:
    return await plan_read(db, supabase, viewer, await _plan(db, viewer, plan_id))


def _starts(spec: LessonSpec, viewer: Viewer) -> datetime:
    return datetime.combine(spec.date, spec.starts_at, tzinfo=viewer.tz)


def _same_week(day: date, other: date, terms: Sequence[TermSpan]) -> bool:
    term = term_on(day, terms)
    if term is not None:
        other_term = term_on(other, terms)
        return other_term == term and term_week(day, term) == term_week(other, term)
    return week_monday(day) == week_monday(other)


async def save_plan(
    db: AsyncSession, supabase: Client, viewer: Viewer, plan_id: uuid.UUID, payload: PlanWrite
) -> PlanSaveResult:
    specs = [await resolve_lesson(db, viewer, key) for key in payload.lessons]
    first = min(specs, key=lambda s: (s.date, s.starts_at))
    for spec in specs:
        if spec.subject_id != first.subject_id or spec.class_name != first.class_name:
            raise invalid("One plan can only cover lessons of the same subject and class")
        if not _same_week(spec.date, first.date, viewer.terms):
            raise invalid("One plan can only cover lessons in the same week")
    if len({s.key().model_dump_json() for s in specs}) != len(specs):
        raise invalid("A lesson is listed twice")

    entry_id = payload.scheme_entry_id
    if entry_id is not None:
        entry = await db.scalar(
            select(SchemeEntry)
            .join(SchemeOfWork, SchemeOfWork.id == SchemeEntry.scheme_id)
            .where(
                SchemeEntry.id == entry_id,
                SchemeEntry.tenant_id == viewer.tenant_id,
                SchemeOfWork.class_name == first.class_name,
                SchemeOfWork.subject_id == first.subject_id,
            )
        )
        if entry is None:
            raise invalid("That scheme entry isn't for this class and subject")

    saved_at = trusted_time(payload.saved_at, viewer.now)
    plan = await db.get(LessonPlan, plan_id)
    if plan is not None and plan.tenant_id != viewer.tenant_id:
        raise _not_found("Lesson plan")
    if plan is not None and saved_at < plan.saved_at:
        # An older copy, sent late: the newer save stays.
        return PlanSaveResult(plan=await plan_read(db, supabase, viewer, plan), stale=True)

    content = {name: getattr(payload, name) for name in PLAN_FIELDS}
    content = {
        k: (v.strip() if k in ("topic", "objectives") else _clean(v)) for k, v in content.items()
    }
    if not content["topic"] or not content["objectives"]:
        raise invalid("A plan needs a topic and objectives")

    term = term_on(first.date, viewer.terms)
    if plan is None:
        plan = LessonPlan(
            id=plan_id,
            tenant_id=viewer.tenant_id,
            written_at=saved_at,
            created_by=viewer.user_id,
            lessons=[],
        )
        db.add(plan)
    else:
        old = {name: getattr(plan, name) for name in PLAN_FIELDS}
        if old != content:
            db.add(
                LessonPlanVersion(
                    tenant_id=viewer.tenant_id,
                    plan_id=plan.id,
                    content=old,
                    saved_by=plan.updated_by or plan.created_by,
                    saved_at=plan.saved_at,
                )
            )
    for name, value in content.items():
        setattr(plan, name, value)
    plan.class_name = first.class_name
    plan.subject_id = first.subject_id
    plan.subject_name = first.subject_name
    plan.term = term.label if term else None
    plan.year = term.year if term else None
    plan.week = term_week(first.date, term) if term else None
    plan.scheme_entry_id = entry_id
    plan.saved_at = saved_at
    plan.updated_by = viewer.user_id
    plan.late = is_late(plan.written_at, _starts(first, viewer))

    rows: list[PlannedLesson] = []
    for spec in specs:
        row = await _planned_lesson(db, viewer, spec)
        if row is None:
            row = _new_planned_lesson(viewer, spec)
            db.add(row)
        elif row.plan_id is not None and row.plan_id != plan.id:
            raise _conflict(
                f"{spec.class_name} {spec.stream or ''} on {spec.date} already has a plan".replace(
                    "  ", " "
                )
            )
        rows.append(row)
    for row in list(plan.lessons):
        if row not in rows:
            if row.record_status is not None:
                raise _conflict("A lesson that has been recorded can't be taken off its plan")
            await db.delete(row)
    plan.lessons = rows
    await db.commit()
    await db.refresh(plan)
    return PlanSaveResult(plan=await plan_read(db, supabase, viewer, plan))


async def delete_plan(db: AsyncSession, viewer: Viewer, plan_id: uuid.UUID) -> None:
    """Its author or an approver, while none of its lessons is recorded."""
    plan = await _plan(db, viewer, plan_id)
    if not viewer.approver and plan.created_by != viewer.user_id:
        raise _forbidden("Only the plan's author or an approver can delete it")
    if any(r.record_status is not None for r in plan.lessons):
        raise _conflict("A plan with recorded lessons can't be deleted")
    for row in list(plan.lessons):
        await db.delete(row)
    await db.delete(plan)
    await db.commit()


async def plan_versions(
    db: AsyncSession, viewer: Viewer, plan_id: uuid.UUID
) -> list[PlanVersionRead]:
    await _plan(db, viewer, plan_id)
    versions = list(
        await db.scalars(
            select(LessonPlanVersion)
            .where(LessonPlanVersion.plan_id == plan_id)
            .order_by(LessonPlanVersion.saved_at.desc())
        )
    )
    names = await user_names(db, (v.saved_by for v in versions))
    return [
        PlanVersionRead(**v.content, saved_by=_person(v.saved_by, names), saved_at=v.saved_at)
        for v in versions
    ]


async def list_plans(
    db: AsyncSession,
    viewer: Viewer,
    *,
    term: str | None,
    year: int | None,
    class_name: str | None,
    subject_id: uuid.UUID | None,
    week: int | None,
    author_id: uuid.UUID | None,
) -> list[PlanSummary]:
    query = select(LessonPlan).where(LessonPlan.tenant_id == viewer.tenant_id)
    if term is not None:
        query = query.where(LessonPlan.term == term)
    if year is not None:
        query = query.where(LessonPlan.year == year)
    if class_name is not None:
        query = query.where(LessonPlan.class_name == class_name)
    if subject_id is not None:
        query = query.where(LessonPlan.subject_id == subject_id)
    if week is not None:
        query = query.where(LessonPlan.week == week)
    if author_id is not None:
        query = query.where(LessonPlan.created_by == author_id)
    plans = list(await db.scalars(query.order_by(LessonPlan.written_at.desc()).limit(500)))
    staff = await _staff_names(db, viewer)
    return [await _plan_summary(db, viewer, p, staff) for p in plans]


# ---------------------------------------------------------------------------
# The record of work
# ---------------------------------------------------------------------------
async def save_record(db: AsyncSession, viewer: Viewer, payload: RecordWrite) -> RecordRead:
    """
    After the lesson. A lesson that didn't happen can be recorded Not taught
    without a plan; Taught and Partly taught need one.
    """
    spec = await resolve_lesson(db, viewer, payload.lesson)
    if spec.date > viewer.today:
        raise invalid("A lesson can be recorded once it has happened")
    row = await _planned_lesson(db, viewer, spec)
    if (row is None or row.plan_id is None) and payload.status != "not_taught":
        raise invalid("Plan the lesson first, or record it as not taught")
    if row is None:
        row = _new_planned_lesson(viewer, spec)
        db.add(row)

    recorded_at = trusted_time(payload.recorded_at, viewer.now)
    if row.recorded_at is None or recorded_at >= row.recorded_at:
        row.record_status = payload.status
        row.not_taught_reason = payload.reason
        row.record_comment = _clean(payload.comment)
        row.reflection = _clean(payload.reflection)
        row.recorded_by = viewer.user_id
        row.recorded_at = recorded_at
        await db.commit()
        await db.refresh(row)
    names = await user_names(db, [row.recorded_by])
    read = record_read(row, names)
    assert read is not None
    return read
