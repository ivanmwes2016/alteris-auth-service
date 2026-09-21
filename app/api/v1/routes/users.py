"""
Endpoints for the Users & Roles page. tenant_id always comes from the
authenticated user. Listing is open to any active member; inviting, changing
roles, removing and resending require Owner or Head Teacher.
"""

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import Client

from app.api.v1.routes.auth import get_current_user
from app.core.db import get_db
from app.core.supabase import get_supabase, get_supabase_auth
from app.crud import team
from app.db.models.users import User
from app.db.schemas.team import (
    AcceptInvite,
    AcceptInviteResponse,
    InviteCreate,
    RoleUpdate,
    TeamMemberRead,
)

router = APIRouter()


@router.get("", response_model=list[TeamMemberRead])
async def list_team_members(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TeamMemberRead]:
    actor = await team.get_actor(db, current_user)
    return await team.list_team(db, actor.tenant_id)


@router.post("/invite", response_model=TeamMemberRead, status_code=status.HTTP_201_CREATED)
async def invite_team_member(
    payload: InviteCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
    supabase_auth: Client = Depends(get_supabase_auth),
) -> TeamMemberRead:
    actor = await team.get_manager(db, current_user)
    return await team.invite_member(db, supabase, supabase_auth, actor, payload)


@router.post("/invites/accept", response_model=AcceptInviteResponse)
async def accept_team_invite(
    payload: AcceptInvite,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AcceptInviteResponse:
    """Called by the invitee after signing in from the emailed link."""
    return await team.accept_invite(db, current_user, payload.token)


@router.patch("/{member_id}", response_model=TeamMemberRead)
async def update_team_member_role(
    member_id: uuid.UUID,
    payload: RoleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TeamMemberRead:
    actor = await team.get_manager(db, current_user)
    return await team.change_role(db, actor, member_id, payload.role)


@router.delete("/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_team_member(
    member_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    actor = await team.get_manager(db, current_user)
    await team.remove_member(db, actor, member_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{member_id}/resend-invite", response_model=TeamMemberRead)
async def resend_team_invite(
    member_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
    supabase_auth: Client = Depends(get_supabase_auth),
) -> TeamMemberRead:
    actor = await team.get_manager(db, current_user)
    return await team.resend_invite(db, supabase, supabase_auth, actor, member_id)
