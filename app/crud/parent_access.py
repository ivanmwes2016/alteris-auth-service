"""
Parents in the app: the school invites a parent by email, the parent sets a password
from the link, and their login is linked to the school's parent record
(parents.user_id). They then read their own children's attendance, timetable,
health and behaviour, and get pushes about them (app.services.parent_push).

A parent is never a member of the school (no tenant_members row), so every staff
route, which all go through app.crud.team.get_actor or get_current_member, refuses
them. Parent routes go through require_child instead: a student is theirs only while
a student_parents row links it to a parent record carrying their login, in a school
that is approved. Removing that link, or the record's login, ends their access on
the next request.

Parents see the health and behaviour records in full, the same as staff do: the
school chose that, so the staff screens say these records are visible to parents.
"""

import hashlib
import logging
import secrets
import uuid
from collections import defaultdict
from collections.abc import Collection
from datetime import UTC, date, datetime, timedelta

from fastapi import HTTPException, status
from gotrue.errors import AuthApiError
from sqlalchemy import Select, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from supabase import Client

from app.core.config import get_settings
from app.crud import attendance, student_access
from app.db.models.academics import SchoolClass, Subject
from app.db.models.parent import Parent, ParentPushSettings
from app.db.models.staff import Staff
from app.db.models.student import Student
from app.db.models.student_parent import StudentParent
from app.db.models.tenant import Tenant
from app.db.models.users import User
from app.db.schemas.parent_access import (
    AppStatus,
    ChildLessonRead,
    ChildOptionsPeriodRead,
    ChildPeriodRead,
    ChildRead,
    ChildSchool,
    ChildTimetableRead,
    ParentAppAccess,
    ParentInviteAccepted,
    ParentInvitePreview,
    PushSettingsRead,
    PushSettingsUpdate,
    Topic,
)
from app.helpers import password as password_rules
from app.helpers.stale_users import remove_if_stale
from app.helpers.timezones import school_timezone
from app.services.email import EmailError, parent_invite_email, send_email

log = logging.getLogger(__name__)

INVITE_TTL = timedelta(days=7)


# ---------------------------------------------------------------------------
# Pure rules (unit-tested without a database)
# ---------------------------------------------------------------------------
def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def app_status(
    *, user_id: uuid.UUID | None, invite_expires_at: datetime | None, now: datetime
) -> AppStatus:
    """The invite's expiry is cleared with its token, once used or cancelled."""
    if user_id is not None:
        return "active"
    if invite_expires_at is None:
        return "not_invited"
    return "invited" if invite_expires_at > now else "expired"


def first_name(name: str | None) -> str:
    parts = (name or "").split()
    return parts[0] if parts else ""


def name_parts(name: str) -> tuple[str, str]:
    """(first, last) for an account's metadata, from the name the school typed."""
    first, _, last = " ".join(name.split()).partition(" ")
    return first, last


def same_email(old: str | None, new: str | None) -> bool:
    """Whether two addresses are the same, ignoring case and spaces (blank is none)."""
    return ((old or "").strip().lower() or None) == ((new or "").strip().lower() or None)


def build_invite_url(token: str) -> str:
    """The web page where the parent sets a password. It signs no one in by opening."""
    return f"{get_settings().FRONTEND_URL.rstrip('/')}/parent-invite?token={token}"


def wants(settings: ParentPushSettings | None, topic: Topic) -> bool:
    """No settings row means every kind of push is on."""
    return settings is None or bool(getattr(settings, topic))


# ---------------------------------------------------------------------------
# The school's side: inviting and removing access
# ---------------------------------------------------------------------------
def access_read(parent: Parent, *, now: datetime | None = None) -> ParentAppAccess:
    return ParentAppAccess(
        parent_id=parent.id,
        status=app_status(
            user_id=parent.user_id,
            invite_expires_at=parent.invite_expires_at,
            now=now or datetime.now(UTC),
        ),
        email=parent.email,
        invited_at=parent.invited_at,
        invite_expires_at=parent.invite_expires_at,
        joined_at=parent.joined_at,
    )


async def _school_parent(db: AsyncSession, tenant_id: uuid.UUID, parent_id: uuid.UUID) -> Parent:
    parent = await db.scalar(
        select(Parent).where(Parent.id == parent_id, Parent.tenant_id == tenant_id)
    )
    if parent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Parent not found")
    return parent


async def _children_names(db: AsyncSession, parent_id: uuid.UUID) -> list[str]:
    names = await db.scalars(
        select(Student.name)
        .join(StudentParent, StudentParent.student_id == Student.id)
        .where(StudentParent.parent_id == parent_id)
        .order_by(Student.name)
    )
    return [first_name(n) or n for n in names]


async def invite(
    db: AsyncSession, caller: student_access.StudentCaller, parent_id: uuid.UUID
) -> ParentAppAccess:
    """
    Email the parent a link to set a password (or a new one: resending replaces the
    link). Allowed again after they joined, which only re-sends; their login stays.
    """
    parent = await _school_parent(db, caller.tenant_id, parent_id)
    email = (parent.email or "").strip().lower()
    if not email:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Add the parent's email address before inviting them."
        )
    children = await _children_names(db, parent.id)
    if not children:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Link the parent to a student before inviting them."
        )
    school_name = await db.scalar(select(Tenant.name).where(Tenant.id == caller.tenant_id))

    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    parent.email = email
    parent.invite_token_hash = hash_token(token)
    parent.invite_expires_at = now + INVITE_TTL
    parent.invited_at = now
    await db.flush()

    subject, html_body, text_body = parent_invite_email(
        invite_url=build_invite_url(token),
        school_name=school_name or "Your school",
        children=children,
        first_name=first_name(parent.name) or None,
    )
    try:
        await send_email(to=email, subject=subject, html_body=html_body, text_body=text_body)
    except EmailError as exc:
        await db.rollback()
        if exc.rate_limited:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many invite emails. Wait a minute and try again.",
            ) from exc
        log.error("Sending a parent invite failed: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Couldn't send the invite email. Try again."
        ) from exc
    await db.commit()
    return access_read(parent, now=now)


async def revoke(
    db: AsyncSession, caller: student_access.StudentCaller, parent_id: uuid.UUID
) -> ParentAppAccess:
    """End the parent's app access and cancel any invite. Their record stays."""
    parent = await _school_parent(db, caller.tenant_id, parent_id)
    clear_access(parent)
    await db.commit()
    return access_read(parent)


def clear_access(parent: Parent) -> None:
    """Unlink the login and drop the invite (also when the school changes the email)."""
    parent.user_id = None
    parent.invite_token_hash = None
    parent.invite_expires_at = None
    parent.invited_at = None
    parent.joined_at = None


# ---------------------------------------------------------------------------
# The invite page
# ---------------------------------------------------------------------------
def _invalid_invite() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "This invitation is invalid or has expired")


async def _invited_parent(db: AsyncSession, token: str, *, lock: bool = False) -> Parent:
    query = select(Parent).where(Parent.invite_token_hash == hash_token(token))
    if lock:
        query = query.with_for_update()
    parent = await db.scalar(query)
    if (
        parent is None
        or not parent.email
        or parent.invite_expires_at is None
        or parent.invite_expires_at <= datetime.now(UTC)
    ):
        raise _invalid_invite()
    return parent


async def _login(db: AsyncSession, email: str) -> tuple[uuid.UUID, bool] | None:
    """The Supabase login for an email: (id, whether the email is confirmed), or None."""
    row = (
        await db.execute(
            text(
                "select id, email_confirmed_at is not null from auth.users "
                "where lower(email) = :email limit 1"
            ),
            {"email": email},
        )
    ).first()
    return None if row is None else (uuid.UUID(str(row[0])), bool(row[1]))


async def preview_invite(db: AsyncSession, token: str) -> ParentInvitePreview:
    """
    What the invite page shows before acting. The token was only ever emailed to the
    parent; missing, expired and used invites all get the same answer.
    """
    parent = await _invited_parent(db, token)
    email = (parent.email or "").strip().lower()
    login = await _login(db, email)
    school_name = await db.scalar(select(Tenant.name).where(Tenant.id == parent.tenant_id))
    return ParentInvitePreview(
        email=email,
        parent_name=parent.name,
        school_name=school_name or "",
        children=await _children_names(db, parent.id),
        has_account=login is not None and login[1],
    )


def _supabase_error(exc: AuthApiError) -> HTTPException:
    if exc.code == "weak_password":
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Choose a stronger password.")
    if exc.status < 500:
        log.warning("Supabase refused a parent login: status=%s code=%s", exc.status, exc.code)
        return HTTPException(
            status.HTTP_400_BAD_REQUEST, "We couldn't create an account with those details."
        )
    log.error("Supabase failed creating a parent login: %s", exc)
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE, "Authentication service unavailable. Try again."
    )


async def _checked_password(password: str | None, email: str) -> str:
    if not password:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Choose a password.")
    new_password = password_rules.normalize(password)
    await password_rules.check(new_password, email)
    return new_password


async def _ensure_user_row(
    db: AsyncSession, supabase: Client, user_id: uuid.UUID, email: str, name: str
) -> None:
    """Our users row for the login, made now so parents.user_id can point at it."""
    if await db.get(User, user_id) is not None:
        return
    old_id = await db.scalar(select(User.id).where(User.email == email, User.id != user_id))
    if old_id is not None:
        await remove_if_stale(db, supabase, old_id)
    db.add(User(id=user_id, email=email, name=name or None))
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This email is linked to an older account. Contact support to restore access.",
        ) from exc


async def accept_invite(
    db: AsyncSession, supabase: Client, token: str, password: str | None
) -> ParentInviteAccepted:
    """
    Opening the emailed link proves the parent owns the address, so this may create
    their login with the password they chose, already confirmed. An address that
    already has a confirmed login is linked as it is; one with an unconfirmed login
    (signed up, never confirmed) gets the chosen password and is confirmed now.
    """
    parent = await _invited_parent(db, token, lock=True)
    email = (parent.email or "").strip().lower()
    login = await _login(db, email)
    first, last = name_parts(parent.name)
    created = False

    try:
        if login is None:
            new_password = await _checked_password(password, email)
            response = await run_in_threadpool(
                supabase.auth.admin.create_user,
                {
                    "email": email,
                    "password": new_password,
                    "email_confirm": True,
                    "user_metadata": {"first_name": first, "last_name": last},
                },
            )
            user_id = uuid.UUID(str(response.user.id))
            created = True
        elif not login[1]:
            new_password = await _checked_password(password, email)
            await run_in_threadpool(
                supabase.auth.admin.update_user_by_id,
                str(login[0]),
                {"password": new_password, "email_confirm": True},
            )
            user_id = login[0]
        else:
            user_id = login[0]
    except AuthApiError as exc:
        raise _supabase_error(exc) from exc

    await _ensure_user_row(db, supabase, user_id, email, parent.name)
    now = datetime.now(UTC)
    parent.user_id = user_id
    parent.joined_at = now
    parent.invite_token_hash = None
    parent.invite_expires_at = None
    school_name = await db.scalar(select(Tenant.name).where(Tenant.id == parent.tenant_id))
    await db.commit()
    return ParentInviteAccepted(email=email, school_name=school_name or "", account_created=created)


# ---------------------------------------------------------------------------
# The parent's side
# ---------------------------------------------------------------------------
def _children_query(user_id: uuid.UUID) -> Select[tuple[Student, str, Tenant]]:
    return (
        select(Student, StudentParent.relationship_type, Tenant)
        .join(StudentParent, StudentParent.student_id == Student.id)
        .join(Parent, Parent.id == StudentParent.parent_id)
        .join(Tenant, Tenant.id == Student.tenant_id)
        .where(
            Parent.user_id == user_id,
            Parent.tenant_id == Student.tenant_id,
            Tenant.status == "approved",
        )
    )


async def is_parent(db: AsyncSession, user_id: uuid.UUID) -> bool:
    found = await db.scalar(_children_query(user_id).with_only_columns(Student.id).limit(1))
    return found is not None


async def parent_school_id(db: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | None:
    """The school of the parent's first child: what their push tokens are filed under."""
    query = _children_query(user_id).with_only_columns(Student.tenant_id).order_by(Student.name)
    found: uuid.UUID | None = await db.scalar(query.limit(1))
    return found


async def children(db: AsyncSession, user: User) -> list[ChildRead]:
    rows = (await db.execute(_children_query(user.id).order_by(Student.name, Student.id))).all()
    seen: set[uuid.UUID] = set()
    result: list[ChildRead] = []
    for student, relationship_type, tenant in rows:
        if student.id in seen:  # linked through two parent records
            continue
        seen.add(student.id)
        result.append(
            ChildRead(
                id=student.id,
                name=student.name,
                admission_number=student.school_id,
                class_name=student.class_applied,
                stream=student.faculty,
                relationship_type=relationship_type,
                school=ChildSchool(id=tenant.id, name=tenant.name),
            )
        )
    return result


async def require_child(db: AsyncSession, user: User, student_id: uuid.UUID) -> Student:
    """The student, if it's this parent's child; 404 otherwise, as if it didn't exist."""
    row = (await db.execute(_children_query(user.id).where(Student.id == student_id))).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found")
    student: Student = row[0]
    return student


async def school_today(db: AsyncSession, tenant_id: uuid.UUID) -> date:
    tenant = await db.get(Tenant, tenant_id)
    tz = school_timezone(
        explicit=tenant.timezone if tenant else None, country=tenant.country if tenant else None
    )
    return attendance.school_today(tz, attendance.now_utc())


async def child_timetable(db: AsyncSession, student: Student) -> ChildTimetableRead:
    """The lessons of the child's class (and stream) on the timetable that applies today."""
    today = await school_today(db, student.tenant_id)
    terms = await attendance.load_terms(db, student.tenant_id)
    timetable = await attendance.school_timetable_on(db, student.tenant_id, terms, today)
    if timetable is None or not student.class_applied:
        return ChildTimetableRead()

    classes = select(SchoolClass.id).where(
        SchoolClass.tenant_id == student.tenant_id, SchoolClass.name == student.class_applied
    )
    if student.faculty:
        classes = classes.where(SchoolClass.stream == student.faculty)
    class_ids = set(await db.scalars(classes))

    lessons = [lesson for lesson in timetable.lessons if lesson.class_id in class_ids]
    subject_names = dict(
        (
            await db.execute(
                select(Subject.id, Subject.name).where(
                    Subject.id.in_({lesson.subject_id for lesson in lessons})
                )
            )
        ).tuples()
    )
    staff_ids = {lesson.staff_id for lesson in lessons if lesson.staff_id}
    staff = {s.id: s for s in await db.scalars(select(Staff).where(Staff.id.in_(staff_ids)))}

    return ChildTimetableRead(
        term=timetable.term,
        year=timetable.year,
        days=list(timetable.days),
        periods=[ChildPeriodRead.model_validate(p) for p in timetable.periods],
        lessons=[
            ChildLessonRead(
                id=lesson.id,
                day=lesson.day,
                period_id=lesson.period_id,
                length=lesson.length,
                subject_name=subject_names.get(lesson.subject_id, "Lesson"),
                teacher_name=attendance.staff_name(staff.get(lesson.staff_id))
                if lesson.staff_id
                else None,
            )
            for lesson in sorted(lessons, key=lambda lesson: (lesson.day, str(lesson.period_id)))
        ],
        options_periods=[
            ChildOptionsPeriodRead(id=o.id, day=o.day, period_id=o.period_id)
            for o in timetable.options_periods
            if o.class_id in class_ids
        ],
    )


# ---------------------------------------------------------------------------
# Push settings, and who gets a push about a student
# ---------------------------------------------------------------------------
async def push_settings(db: AsyncSession, user: User) -> PushSettingsRead:
    row = await db.get(ParentPushSettings, user.id)
    return PushSettingsRead.model_validate(row) if row else PushSettingsRead()


async def update_push_settings(
    db: AsyncSession, user: User, payload: PushSettingsUpdate
) -> PushSettingsRead:
    if not await is_parent(db, user.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only parents have these settings.")
    row = await db.get(ParentPushSettings, user.id)
    if row is None:
        row = ParentPushSettings(
            user_id=user.id, attendance=True, results=True, health=True, behaviour=True
        )
        db.add(row)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(row, field, value)
    await db.commit()
    return PushSettingsRead.model_validate(row)


async def recipients(
    db: AsyncSession, student_ids: Collection[uuid.UUID], topic: Topic
) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Each student's parents' logins that want pushes about `topic`."""
    if not student_ids:
        return {}
    rows = await db.execute(
        select(StudentParent.student_id, Parent.user_id, ParentPushSettings)
        .join(Parent, Parent.id == StudentParent.parent_id)
        .join(Student, Student.id == StudentParent.student_id)
        .join(Tenant, Tenant.id == Student.tenant_id)
        .outerjoin(ParentPushSettings, ParentPushSettings.user_id == Parent.user_id)
        .where(
            StudentParent.student_id.in_(student_ids),
            Parent.user_id.is_not(None),
            Parent.tenant_id == Student.tenant_id,
            Tenant.status == "approved",
        )
    )
    found: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for student_id, user_id, settings in rows.tuples():
        if user_id is not None and wants(settings, topic):
            found[student_id].add(user_id)
    return found
