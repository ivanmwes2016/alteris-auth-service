from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import Client

from app.core.db import get_db
from app.core.supabase import get_supabase, get_supabase_auth
from app.db.models.auth import LoginRequest, TokenResponse
from app.db.models.role import Role
from app.db.models.tenant import Tenant
from app.db.models.tenant_member import TenantMember
from app.db.models.users import User
from app.helpers.jwt import get_user_from_token
from app.helpers.last_active import touch_last_active
from app.helpers.user_context import get_user_context
from app.services.auth_service import AuthService

router = APIRouter()


def get_auth_service(
    supabase: Client = Depends(get_supabase_auth),
    db: AsyncSession = Depends(get_db),
) -> AuthService:
    return AuthService(supabase=supabase, db=db)


class CurrentUserResponse(BaseModel):
    id: UUID
    email: str
    name: str | None = None


class CurrentTenantResponse(BaseModel):
    id: UUID
    name: str
    workspace_id: str | None = None
    logo_path: str | None = None
    plan: str


class MeResponse(BaseModel):
    user: CurrentUserResponse
    tenant: CurrentTenantResponse | None = None
    role: str | None = None
    subscription_active: bool


class SessionResponse(BaseModel):
    ok: bool
    user_id: UUID


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Invalid email or password"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Authentication unavailable"},
    },
)
async def login(
    payload: LoginRequest,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    return await service.login(email=payload.email, password=payload.password)


@router.get("/me", response_model=MeResponse)
async def me(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    supabase: Client = Depends(get_supabase),
) -> MeResponse:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing token",
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid auth header",
        )

    token = authorization.removeprefix("Bearer ").strip()

    user = get_user_from_token(supabase, token)

    user_id = user.id
    email = user.email
    name = user.user_metadata.get("name")

    user_context = await get_user_context(
        db,
        user_id,
        supabase,
    )

    return MeResponse(
        user=CurrentUserResponse(
            id=user_id,
            email=email,
            name=name,
        ),
        tenant=user_context.get("tenant"),
        role=user_context.get("role"),
        subscription_active=bool(user_context.get("subscription_active")),
    )


@router.post("/session", response_model=SessionResponse)
async def create_session(
    response: Response,
    authorization: str | None = Header(default=None),
    supabase: Client = Depends(get_supabase),
) -> SessionResponse:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing token",
        )

    token = authorization.removeprefix("Bearer ").strip()
    user = get_user_from_token(supabase, token)

    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )

    return SessionResponse(
        ok=True,
        user_id=user.id,
    )


async def get_current_user(
    background_tasks: BackgroundTasks,
    authorization: str = Header(None),
    db: AsyncSession = Depends(get_db),
    supabase: Client = Depends(get_supabase),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")

    token = authorization.split(" ")[1]
    supabase_user = get_user_from_token(supabase, token)

    user = await db.get(User, supabase_user.id)

    if not user:
        user = User(
            id=supabase_user.id,
            email=supabase_user.email,
        )
        db.add(user)
        await db.flush()

    background_tasks.add_task(touch_last_active, user.id)

    return user


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """
    Called after the client has already created (or signed into) their Supabase
    account — get_current_user has a local `users` row for them by this point.
    This just creates their tenant and makes them its owner.

    Expected payload:
    {
      name, slug, workspace_id, country
    }
    """
    has_workspace = await db.scalar(
        select(TenantMember.id).where(
            TenantMember.user_id == current_user.id, TenantMember.status == "active"
        )
    )
    if has_workspace is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Your account already belongs to a workspace")

    # "Owner" isn't a stored role — the creator gets `admin` and is recorded as the
    # owner via tenants.owner_id, matching how the rest of the API treats ownership.
    admin_role = (await db.execute(select(Role).where(Role.name == "admin"))).scalar_one()

    tenant = Tenant(
        name=payload.get("name"),
        slug=payload.get("slug"),
        plan="free",
        workspace_id=payload.get("workspace_id"),
        seat_limit=15,
        owner_id=current_user.id,
        country=payload.get("country"),
    )
    db.add(tenant)
    await db.flush()

    now = datetime.now(UTC)
    db.add(
        TenantMember(
            tenant_id=tenant.id,
            user_id=current_user.id,
            role_id=admin_role.id,
            status="active",
            joined_at=now,
            created_at=now,
            last_active_at=now,
        )
    )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "That workspace URL is already taken"
        ) from exc

    return {
        "message": "signup successful",
        "tenant_id": str(tenant.id),
    }


async def get_current_tenant_id(
    db: AsyncSession,
    current_user: User,
) -> UUID:
    result = await db.execute(
        select(TenantMember.tenant_id).where(
            TenantMember.user_id == current_user.id,
            TenantMember.status == "active",
        )
    )

    tenant_id = result.scalar_one_or_none()

    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not belong to an active tenant",
        )

    return tenant_id
