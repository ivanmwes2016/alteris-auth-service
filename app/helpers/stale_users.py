import logging
import uuid

from gotrue.errors import AuthApiError
from sqlalchemy import delete, exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from supabase import Client

from app.db.models.invite import Invite
from app.db.models.profile import Profile
from app.db.models.tenant import Tenant
from app.db.models.tenant_member import TenantMember
from app.db.models.users import User

log = logging.getLogger(__name__)


async def _has_attachments(db: AsyncSession, user_id: uuid.UUID) -> bool:
    return bool(
        await db.scalar(
            select(
                or_(
                    exists().where(TenantMember.user_id == user_id),
                    exists().where(Tenant.owner_id == user_id),
                    exists().where(Invite.invited_by == user_id),
                )
            )
        )
    )


async def _login_is_gone(supabase: Client, user_id: uuid.UUID) -> bool:
    try:
        await run_in_threadpool(supabase.auth.admin.get_user_by_id, str(user_id))
    except AuthApiError as exc:
        return exc.status == 404
    except Exception:
        log.exception("Couldn't check whether Supabase user %s still exists", user_id)
        return False
    return False


async def remove_if_stale(db: AsyncSession, supabase: Client, user_id: uuid.UUID) -> bool:
    """
    Delete a users row left behind by a deleted Supabase account. Returns True if it did.

    Only when Supabase confirms the login is gone AND nothing hangs off the row — no
    memberships, owned school or sent invites — so no one's access is ever broken.
    Anything uncertain (Supabase error, a reference we didn't expect) keeps the row.
    """
    if await _has_attachments(db, user_id) or not await _login_is_gone(supabase, user_id):
        return False

    try:
        async with db.begin_nested():
            await db.execute(delete(Profile).where(Profile.user_id == user_id))
            await db.execute(delete(User).where(User.id == user_id))
    except IntegrityError:
        # Some other table still references this user; leave it for a person to decide.
        log.warning("Leftover user %s is still referenced elsewhere; kept", user_id)
        return False

    log.info("Removed leftover user %s whose Supabase account no longer exists", user_id)
    return True
