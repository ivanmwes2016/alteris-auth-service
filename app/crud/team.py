"""
Service layer for the Users & Roles page.

The team list is a union of two tables: TenantMember rows (Active/Suspended)
and pending Invite rows (Invited). TenantMember.user_id is NOT NULL, so
someone without an account yet can only exist as an invite. Both kinds share
one id space for the /users/{id} routes (UUIDs, so they can't collide).

"Owner" is not a stored role. The workspace creator is recorded on
tenants.owner_id and keeps the `admin` role in the database (so /auth/me and
any frontend checks on it are unchanged); the API presents them as "Owner".

Every query is scoped to the caller's tenant; ids from other tenants 404.
"""

import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from gotrue.errors import AuthApiError
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from supabase import Client

from app.core.config import get_settings
from app.db.models.invite import Invite
from app.db.models.profile import Profile
from app.db.models.role import Role
from app.db.models.tenant import Tenant
from app.db.models.tenant_member import TenantMember
from app.db.models.users import User
from app.db.schemas.team import (
    AcceptInviteResponse,
    InviteCreate,
    TeamMemberRead,
    TeamRole,
)

log = logging.getLogger(__name__)

INVITE_TTL = timedelta(days=7)
OWNER_LABEL = "Owner"

ROLE_DB_NAME: dict[TeamRole, str] = {
    TeamRole.head_teacher: "head_teacher",
    TeamRole.admin: "admin",
    TeamRole.admissions_officer: "admissions_officer",
    TeamRole.teacher: "teacher",
    TeamRole.bursar: "bursar",
}
DB_ROLE_LABEL: dict[str, str] = {db_name: role.value for role, db_name in ROLE_DB_NAME.items()}
TEAM_DB_ROLES = list(DB_ROLE_LABEL)

MEMBER_STATUS_LABEL = {"active": "Active", "suspended": "Suspended"}


# ---------------------------------------------------------------------------
# Pure rules (unit-tested without a database)
# ---------------------------------------------------------------------------
def display_role(role_name: str, *, is_owner: bool) -> str:
    if is_owner:
        return OWNER_LABEL
    return DB_ROLE_LABEL.get(role_name, role_name.replace("_", " ").title())


def can_manage_users(*, is_owner: bool, role_name: str) -> bool:
    """Matches the page's permission matrix: only Owner and Head Teacher manage users."""
    return is_owner or role_name == "head_teacher"


def seats_available(*, seat_limit: int | None, members: int, pending_invites: int = 0) -> bool:
    """A NULL seat_limit means unlimited. Pending invites hold a seat."""
    return seat_limit is None or members + pending_invites < seat_limit


def _frontend_url(path: str, param: str, token: str) -> str:
    return f"{get_settings().FRONTEND_URL.rstrip('/')}{path}?{param}={token}"


def build_signup_url(token: str) -> str:
    """Where a brand-new invitee lands: the signup page, which sees the invite token."""
    return _frontend_url("/signup", "invite", token)


def build_accept_url(token: str) -> str:
    """Where an invitee with an existing account lands to accept."""
    return _frontend_url("/accept-invite", "token", token)


def _already_registered(exc: AuthApiError) -> bool:
    return exc.code in {"email_exists", "user_already_exists"} or (
        "already been registered" in exc.message.lower()
    )


# ---------------------------------------------------------------------------
# Caller context / permissions
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role_name: str
    is_owner: bool


async def get_actor(db: AsyncSession, user: User) -> Actor:
    row = (
        await db.execute(
            select(TenantMember.tenant_id, Role.name, Tenant.owner_id)
            .join(Role, Role.id == TenantMember.role_id)
            .join(Tenant, Tenant.id == TenantMember.tenant_id)
            .where(TenantMember.user_id == user.id, TenantMember.status == "active")
            .order_by(TenantMember.joined_at)
        )
    ).first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not belong to an active tenant",
        )

    tenant_id, role_name, owner_id = row
    return Actor(
        user_id=user.id, tenant_id=tenant_id, role_name=role_name, is_owner=owner_id == user.id
    )


async def get_manager(db: AsyncSession, user: User) -> Actor:
    actor = await get_actor(db, user)
    if not can_manage_users(is_owner=actor.is_owner, role_name=actor.role_name):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to manage users",
        )
    return actor


# ---------------------------------------------------------------------------
# Row -> response helpers
# ---------------------------------------------------------------------------
def _member_read(
    member: TenantMember,
    user: User,
    role: Role,
    full_name: str | None,
    owner_id: uuid.UUID | None,
) -> TeamMemberRead:
    return TeamMemberRead(
        id=member.id,
        name=user.name or full_name,
        email=user.email,
        role=display_role(role.name, is_owner=member.user_id == owner_id),
        lastActive=member.last_active_at,
        status=MEMBER_STATUS_LABEL.get(member.status, member.status.title()),
    )


def _invite_read(invite: Invite, role: Role) -> TeamMemberRead:
    return TeamMemberRead(
        id=invite.id,
        name=" ".join(part for part in (invite.first_name, invite.last_name) if part) or None,
        email=invite.email,
        role=display_role(role.name, is_owner=False),
        lastActive=None,
        status="Invited",
    )


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------
async def _get_owner_id(db: AsyncSession, tenant_id: uuid.UUID) -> uuid.UUID | None:
    return await db.scalar(select(Tenant.owner_id).where(Tenant.id == tenant_id))


async def _role_by_name(db: AsyncSession, name: str) -> Role:
    role = (await db.execute(select(Role).where(Role.name == name))).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=500, detail=f"Role '{name}' does not exist")
    return role


async def _lock_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    """Row-lock the tenant so concurrent invites/accepts can't both take the last seat."""
    return (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    ).scalar_one()


async def _count_members(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    return (
        await db.scalar(
            select(func.count())
            .select_from(TenantMember)
            .where(TenantMember.tenant_id == tenant_id)
        )
    ) or 0


async def _count_pending(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    return (
        await db.scalar(
            select(func.count())
            .select_from(Invite)
            .where(Invite.tenant_id == tenant_id, Invite.accepted_at.is_(None))
        )
    ) or 0


async def _get_member_row(
    db: AsyncSession, tenant_id: uuid.UUID, target_id: uuid.UUID
) -> tuple[TenantMember, User, Role, str | None] | None:
    row = (
        await db.execute(
            select(TenantMember, User, Role, Profile.full_name)
            .join(User, User.id == TenantMember.user_id)
            .join(Role, Role.id == TenantMember.role_id)
            .outerjoin(Profile, Profile.user_id == User.id)
            .where(TenantMember.id == target_id, TenantMember.tenant_id == tenant_id)
        )
    ).first()
    return None if row is None else (row[0], row[1], row[2], row[3])


async def _get_invite_row(
    db: AsyncSession, tenant_id: uuid.UUID, target_id: uuid.UUID
) -> tuple[Invite, Role] | None:
    row = (
        await db.execute(
            select(Invite, Role)
            .join(Role, Role.id == Invite.role_id)
            .where(
                Invite.id == target_id,
                Invite.tenant_id == tenant_id,
                Invite.accepted_at.is_(None),
            )
        )
    ).first()
    return None if row is None else (row[0], row[1])


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team member not found")


def _invite_email_data(invite: Invite, role: Role, workspace_name: str) -> dict[str, str]:
    """
    Values the Supabase "Invite user" template can use as {{ .Data.<key> }}. Supabase also
    stores them on the new account's user_metadata, hence the invited_* names: they're a
    snapshot from invite time, not live account data.
    """
    data = {
        "workspace_name": workspace_name,
        "invited_role": display_role(role.name, is_owner=False),
    }
    if invite.first_name:
        data["first_name"] = invite.first_name
    if invite.last_name:
        data["last_name"] = invite.last_name
    return data


# ---------------------------------------------------------------------------
# Email delivery (Supabase Auth emails)
# ---------------------------------------------------------------------------
async def _send_invite_email(
    supabase: Client, supabase_auth: Client, email: str, token: str, data: dict[str, str]
) -> None:
    """
    Make sure the invitee gets an email; raises if none could be sent.

    A new address gets Supabase's invite email: the link signs them in and lands them on the
    signup page, where they set their password. Supabase won't send that to an address that
    already has an account, so those get a one-time sign-in link to the accept page instead
    (that email can't be personalised per invite — Supabase ignores `data` for existing users).
    """
    try:
        try:
            await run_in_threadpool(
                supabase.auth.admin.invite_user_by_email,
                email,
                {"redirect_to": build_signup_url(token), "data": data},
            )
        except AuthApiError as exc:
            if not _already_registered(exc):
                raise
            await run_in_threadpool(
                supabase_auth.auth.sign_in_with_otp,
                {
                    "email": email,
                    "options": {
                        "email_redirect_to": build_accept_url(token),
                        "should_create_user": False,
                    },
                },
            )
        return
    except AuthApiError as exc:
        if exc.status == status.HTTP_429_TOO_MANY_REQUESTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many invite emails — wait a minute and try again",
            ) from exc
        log.exception("Supabase rejected the invite email")
    except Exception:
        log.exception("Sending the invite email failed")

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Couldn't send the invite email. Try again.",
    )


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
async def list_team(db: AsyncSession, tenant_id: uuid.UUID) -> list[TeamMemberRead]:
    owner_id = await _get_owner_id(db, tenant_id)

    member_rows = (
        await db.execute(
            select(TenantMember, User, Role, Profile.full_name)
            .join(User, User.id == TenantMember.user_id)
            .join(Role, Role.id == TenantMember.role_id)
            .outerjoin(Profile, Profile.user_id == User.id)
            .where(TenantMember.tenant_id == tenant_id, Role.name.in_(TEAM_DB_ROLES))
            # joined_at/created_at have no DB default and are NULL for most rows, so fall
            # back to email to keep the order stable between requests.
            .order_by(
                func.coalesce(TenantMember.joined_at, TenantMember.created_at).asc().nulls_last(),
                func.lower(User.email),
            )
        )
    ).all()
    invite_rows = (
        await db.execute(
            select(Invite, Role)
            .join(Role, Role.id == Invite.role_id)
            .where(
                Invite.tenant_id == tenant_id,
                Invite.accepted_at.is_(None),
                Role.name.in_(TEAM_DB_ROLES),
            )
            .order_by(Invite.created_at)
        )
    ).all()

    team = [_member_read(m, u, r, full_name, owner_id) for m, u, r, full_name in member_rows]
    team.sort(key=lambda person: person.role != OWNER_LABEL)  # stable: Owner first
    team.extend(_invite_read(invite, role) for invite, role in invite_rows)
    return team


async def invite_member(
    db: AsyncSession, supabase: Client, supabase_auth: Client, actor: Actor, payload: InviteCreate
) -> TeamMemberRead:
    tenant = await _lock_tenant(db, actor.tenant_id)
    email = payload.email

    already_member = await db.scalar(
        select(TenantMember.id)
        .join(User, User.id == TenantMember.user_id)
        .where(TenantMember.tenant_id == tenant.id, func.lower(User.email) == email)
    )
    if already_member is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That person is already on your team")

    already_invited = await db.scalar(
        select(Invite.id).where(
            Invite.tenant_id == tenant.id, Invite.email == email, Invite.accepted_at.is_(None)
        )
    )
    if already_invited is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "That email already has a pending invite — resend it instead"
        )

    if not seats_available(
        seat_limit=tenant.seat_limit,
        members=await _count_members(db, tenant.id),
        pending_invites=await _count_pending(db, tenant.id),
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Seat limit reached")

    role = await _role_by_name(db, ROLE_DB_NAME[payload.role])
    invite = Invite(
        tenant_id=tenant.id,
        role_id=role.id,
        email=email,
        first_name=payload.first_name,
        last_name=payload.last_name,
        token=secrets.token_urlsafe(32),
        expires_at=datetime.now(UTC) + INVITE_TTL,
        invited_by=actor.user_id,
    )
    db.add(invite)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "That email already has a pending invite — resend it instead"
        ) from exc

    # Send before committing: if delivery fails the invite is rolled back too,
    # so the admin can simply retry instead of finding an un-notified ghost.
    await _send_invite_email(
        supabase, supabase_auth, email, invite.token, _invite_email_data(invite, role, tenant.name)
    )
    await db.commit()
    return _invite_read(invite, role)


async def change_role(
    db: AsyncSession, actor: Actor, target_id: uuid.UUID, new_role: TeamRole
) -> TeamMemberRead:
    role = await _role_by_name(db, ROLE_DB_NAME[new_role])
    owner_id = await _get_owner_id(db, actor.tenant_id)

    member_row = await _get_member_row(db, actor.tenant_id, target_id)
    if member_row is not None:
        member, user, _old_role, full_name = member_row
        if member.user_id == owner_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "The Owner's role can't be changed")
        member.role_id = role.id
        await db.commit()
        return _member_read(member, user, role, full_name, owner_id)

    invite_row = await _get_invite_row(db, actor.tenant_id, target_id)
    if invite_row is not None:
        invite, _old_role = invite_row
        invite.role_id = role.id
        await db.commit()
        return _invite_read(invite, role)

    raise _not_found()


async def remove_member(db: AsyncSession, actor: Actor, target_id: uuid.UUID) -> None:
    owner_id = await _get_owner_id(db, actor.tenant_id)

    member_row = await _get_member_row(db, actor.tenant_id, target_id)
    if member_row is not None:
        member = member_row[0]
        if member.user_id == owner_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "The Owner can't be removed")
        await db.delete(member)
        await db.commit()
        return

    invite_row = await _get_invite_row(db, actor.tenant_id, target_id)
    if invite_row is not None:
        await db.delete(invite_row[0])
        await db.commit()
        return

    raise _not_found()


async def resend_invite(
    db: AsyncSession,
    supabase: Client,
    supabase_auth: Client,
    actor: Actor,
    target_id: uuid.UUID,
) -> TeamMemberRead:
    invite_row = await _get_invite_row(db, actor.tenant_id, target_id)
    if invite_row is None:
        if await _get_member_row(db, actor.tenant_id, target_id) is not None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only pending invites can be resent")
        raise _not_found()

    invite, role = invite_row
    # A fresh token invalidates any link already sitting in the invitee's inbox.
    invite.token = secrets.token_urlsafe(32)
    invite.expires_at = datetime.now(UTC) + INVITE_TTL
    await db.flush()

    workspace_name = await db.scalar(select(Tenant.name).where(Tenant.id == actor.tenant_id))
    await _send_invite_email(
        supabase,
        supabase_auth,
        invite.email,
        invite.token,
        _invite_email_data(invite, role, workspace_name or ""),
    )
    await db.commit()
    return _invite_read(invite, role)


async def accept_invite(db: AsyncSession, user: User, token: str) -> AcceptInviteResponse:
    invalid = HTTPException(
        status.HTTP_400_BAD_REQUEST, "This invite link is invalid or has expired"
    )

    invite = (
        await db.execute(select(Invite).where(Invite.token == token).with_for_update())
    ).scalar_one_or_none()
    if invite is None or invite.accepted_at is not None or invite.expires_at <= datetime.now(UTC):
        raise invalid

    # The token alone isn't enough: whoever redeems it must be signed in as the
    # invited address, so a leaked link can't be used by someone else.
    if invite.email != user.email.strip().lower():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "This invite was sent to a different email address"
        )

    # The rest of the API assumes one active workspace per user.
    has_workspace = await db.scalar(
        select(TenantMember.id).where(
            TenantMember.user_id == user.id, TenantMember.status == "active"
        )
    )
    if has_workspace is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Your account already belongs to a workspace")

    tenant = await _lock_tenant(db, invite.tenant_id)
    if not seats_available(
        seat_limit=tenant.seat_limit, members=await _count_members(db, tenant.id)
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Seat limit reached")

    role = (await db.execute(select(Role).where(Role.id == invite.role_id))).scalar_one()
    now = datetime.now(UTC)
    db.add(
        TenantMember(
            tenant_id=tenant.id,
            user_id=user.id,
            role_id=role.id,
            status="active",
            joined_at=now,
            created_at=now,
            last_active_at=now,
        )
    )
    invite.accepted_at = now

    # Carry the name the inviter typed over to the account, without overwriting one it has.
    invited_name = " ".join(part for part in (invite.first_name, invite.last_name) if part)
    if invited_name:
        await db.execute(
            update(User)
            .where(User.id == user.id, or_(User.name.is_(None), User.name == ""))
            .values(name=invited_name)
        )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "You're already a member of this workspace"
        ) from exc

    return AcceptInviteResponse(tenantId=tenant.id, role=display_role(role.name, is_owner=False))
