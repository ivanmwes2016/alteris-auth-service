import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import Client

from app.core.config import get_settings
from app.crud import parent_access
from app.crud.class_teacher import own_class
from app.crud.staff_access import can_edit_staff, can_manage_staff_health
from app.crud.student_access import student_access
from app.db.models.role import Role
from app.db.models.tenant import Tenant
from app.db.models.tenant_member import TenantMember
from app.db.models.users import User

log = logging.getLogger(__name__)
config = get_settings()


def trial_expired(tenant: Tenant) -> bool:
    """Approved, past the end of its free trial, and still on the free plan."""
    return (
        tenant.status == "approved"
        and tenant.trial_ends_at is not None
        and tenant.trial_ends_at <= datetime.now(UTC)
        and (tenant.plan or "free") == "free"
    )


def _logo_url(supabase: Client, tenant: Tenant) -> str:
    if not tenant.logo_path:
        return ""
    signed = supabase.storage.from_(config.SUPABASE_LOGO_BUCKET_NAME).create_signed_url(
        tenant.logo_path,
        60 * 60,  # 1 hour
    )
    return str(signed["signedURL"])


def _tenant_read(tenant: Tenant, logo_url: str) -> dict[str, Any]:
    return {
        "id": str(tenant.id),
        "name": tenant.name,
        "workspace_id": tenant.workspace_id,
        "logo_path": logo_url,
        "plan": tenant.plan,
        "status": tenant.status,
        "trial_ends_at": tenant.trial_ends_at,
        "trial_expired": trial_expired(tenant),
    }


async def _parent_context(
    db: AsyncSession, school_id: uuid.UUID, supabase: Client
) -> dict[str, Any]:
    """
    A parent: role "parent", and their first child's school so the app can show its
    name and logo. They get no student access or staff rights; their own routes are
    under /parent (app.crud.parent_access).
    """
    tenant = await db.get(Tenant, school_id)
    return {
        "tenant": _tenant_read(tenant, _logo_url(supabase, tenant)) if tenant else None,
        "role": "parent",
        "is_owner": False,
        "student_access": None,
        "can_edit_staff": False,
        "can_manage_staff_health": False,
        "class_teacher_of": None,
        "is_parent": True,
        "subscription_active": False,
    }


async def get_user_context(db: AsyncSession, user_id: str, supabase: Client) -> dict[str, Any]:
    try:
        result = await db.execute(
            select(TenantMember, Tenant, Role)
            .join(Tenant, Tenant.id == TenantMember.tenant_id)
            .join(Role, Role.id == TenantMember.role_id)
            .where(TenantMember.user_id == user_id, TenantMember.status == "active")
            # The same membership the routes act in (app.crud.team.get_actor).
            .order_by(TenantMember.joined_at)
        )

        row = result.first()

        if not row:
            # Not on any school's staff: a parent, if a school has linked their login.
            parent_school = await parent_access.parent_school_id(db, uuid.UUID(str(user_id)))
            if parent_school is not None:
                return await _parent_context(db, parent_school, supabase)
            return {
                "tenant": None,
                "role": None,
                "is_parent": False,
                "is_owner": False,
                "student_access": None,
                "can_edit_staff": False,
                "can_manage_staff_health": False,
                "class_teacher_of": None,
                "subscription_active": False,
            }

        _, tenant, role = row
        is_owner = tenant.owner_id is not None and str(tenant.owner_id) == str(user_id)

        user = await db.get(User, uuid.UUID(str(user_id)))
        class_teacher_of = await own_class(db, tenant_id=tenant.id, user=user) if user else None

        return {
            "tenant": _tenant_read(tenant, _logo_url(supabase, tenant)),
            "role": role.name,
            "is_owner": is_owner,
            "student_access": student_access(is_owner=is_owner, role_name=role.name).as_dict(),
            "can_edit_staff": can_edit_staff(is_owner=is_owner, role_name=role.name),
            "can_manage_staff_health": can_manage_staff_health(
                is_owner=is_owner, role_name=role.name
            ),
            "class_teacher_of": class_teacher_of,
            # Staff who are also a parent at a school using the app.
            "is_parent": await parent_access.is_parent(db, uuid.UUID(str(user_id))),
            "subscription_active": True,  # To Do ==> get from stripe subscription
        }

    except SQLAlchemyError as exc:
        log.exception("Failed to load user context")
        await db.rollback()

        raise HTTPException(
            status_code=500,
            detail="Failed to load user context",
        ) from exc
