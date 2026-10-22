import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from gotrue import User as SupabaseUser
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import Client

from app.core.db import get_db
from app.core.supabase import get_supabase, get_supabase_auth
from app.db.models.auth import (
    AcceptedResponse,
    EmailRequest,
    LoginRequest,
    PasswordChangeRequest,
    RegisterRequest,
    TokenResponse,
)
from app.db.models.role import Role
from app.db.models.tenant import Tenant
from app.db.models.tenant_member import TenantMember
from app.db.models.users import User
from app.helpers import password as password_rules
from app.helpers import rate_limit
from app.helpers.jwt import get_user_from_token
from app.helpers.last_active import touch_last_active
from app.helpers.phone import clean_phone
from app.helpers.rate_limit import client_ip
from app.helpers.stale_users import remove_if_stale
from app.helpers.user_context import get_user_context
from app.services import account
from app.services.auth_service import AuthService
from app.services.notifications import (
    notify_owner_school_received,
    notify_school_registered,
)
from app.services.school_approval import new_review_token, review_url

log = logging.getLogger(__name__)

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
    # "pending" until the platform team approves the school; nothing works before then.
    status: str = "approved"
    trial_ends_at: datetime | None = None
    # The free trial is over and the school hasn't moved to a paid plan.
    trial_expired: bool = False


class MeResponse(BaseModel):
    user: CurrentUserResponse
    tenant: CurrentTenantResponse | None = None
    role: str | None = None
    subscription_active: bool


class SessionResponse(BaseModel):
    ok: bool
    user_id: UUID


def display_name(metadata: dict[str, Any] | None, stored_name: str | None) -> str | None:
    """
    The person's name for display. Their own signup metadata (first_name/last_name,
    set by both signup forms) wins; then the name an inviter typed (users.name); then
    older metadata keys.
    """
    meta = metadata or {}

    def text(key: str) -> str:
        value = meta.get(key)
        return " ".join(value.split()) if isinstance(value, str) else ""

    return (
        " ".join(part for part in (text("first_name"), text("last_name")) if part)
        or (stored_name or "").strip()
        or text("full_name")
        or text("name")
        or None
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Invalid email or password"},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many attempts"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Authentication unavailable"},
    },
)
async def login(
    payload: LoginRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """
    Every attempt counts against the caller's IP and the account. Only a successful
    sign-in (which needs the password) clears the account's count, so guessing stops
    after a few tries however many IPs the guesses come from. Unknown emails are
    counted the same way, so the limit says nothing about which accounts exist.
    """
    await rate_limit.enforce("login_ip", client_ip(request))
    await rate_limit.enforce("login_account", payload.email)
    tokens = await service.login(email=payload.email, password=payload.password)
    await rate_limit.reset("login_account", payload.email)
    return tokens


@router.post("/register", response_model=AcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def register(
    payload: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    supabase: Client = Depends(get_supabase),
) -> AcceptedResponse:
    """
    Create an account and email a confirmation link (via Resend). The same answer
    whether or not the email already has an account.
    """
    message = await account.register(db, supabase, payload, client_ip(request))
    return AcceptedResponse(message=message)


@router.post(
    "/verification/resend",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resend_verification(
    payload: EmailRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    supabase: Client = Depends(get_supabase),
) -> AcceptedResponse:
    message = await account.resend_verification(db, supabase, payload, client_ip(request))
    return AcceptedResponse(message=message)


@router.post(
    "/password/forgot",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def forgot_password(
    payload: EmailRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    supabase: Client = Depends(get_supabase),
) -> AcceptedResponse:
    message = await account.forgot_password(db, supabase, payload, client_ip(request))
    return AcceptedResponse(message=message)


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
    await rate_limit.enforce("api_user", str(user.id), sensitive=False)

    user_id = user.id
    email = user.email
    stored_name = await db.scalar(select(User.name).where(User.id == user_id))
    name = display_name(user.user_metadata, stored_name)

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
    await rate_limit.enforce("api_user", str(user.id), sensitive=False)

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


def get_supabase_user(
    authorization: str = Header(None),
    supabase: Client = Depends(get_supabase),
) -> SupabaseUser:
    """The caller's Supabase account, checked live. FastAPI runs this once per request."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")

    token = authorization.split(" ")[1]
    return get_user_from_token(supabase, token)


async def get_current_user(
    background_tasks: BackgroundTasks,
    supabase_user: SupabaseUser = Depends(get_supabase_user),
    db: AsyncSession = Depends(get_db),
    supabase: Client = Depends(get_supabase),
) -> User:
    # Every authenticated route depends on this; checked before touching the database.
    await rate_limit.enforce("api_user", str(supabase_user.id), sensitive=False)
    user = await db.get(User, supabase_user.id)

    if not user:
        # A Supabase account that was deleted and signed up again gets a new id, but its
        # old users row still holds the email. Clear it if it's a pure leftover; one that
        # still has memberships or a school is kept (not merged into this login either).
        old_id = await db.scalar(
            select(User.id).where(User.email == supabase_user.email, User.id != supabase_user.id)
        )
        if old_id is not None:
            await remove_if_stale(db, supabase, old_id)

        user = User(
            id=supabase_user.id,
            email=supabase_user.email,
        )
        db.add(user)
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            log.warning(
                "Supabase user %s has an email already held by another users row",
                supabase_user.id,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This email is linked to an older account. "
                "Contact support to restore access.",
            ) from exc

    background_tasks.add_task(touch_last_active, user.id)

    return user


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: PasswordChangeRequest,
    supabase_user: SupabaseUser = Depends(get_supabase_user),
    supabase: Client = Depends(get_supabase),
) -> Response:
    """
    Set the signed-in user's password (after a reset link, or when joining from an
    invite), checked against the password rules first. The frontend calls this instead
    of Supabase's updateUser, which can't apply them.
    """
    await rate_limit.enforce("password_change", str(supabase_user.id))
    new_password = password_rules.normalize(payload.password)
    await password_rules.check(new_password, supabase_user.email)
    await account.set_password(supabase, str(supabase_user.id), new_password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


INVALID_PHONE = "Please enter a valid phone number, e.g. +256 700 123456."


def _short_answer(value: object) -> str | None:
    """An optional sign-up answer (school type, how they heard of us), tidied."""
    if not isinstance(value, str):
        return None
    return " ".join(value.split())[:100] or None


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    supabase_user: SupabaseUser = Depends(get_supabase_user),
) -> dict[str, str]:
    """
    Called after the client has already created (or signed into) their Supabase
    account — get_current_user has a local `users` row for them by this point.
    This just creates their tenant and makes them its owner.

    Expected payload:
    {
      name, slug, workspace_id, country, phone (required), type, source
    }
    """
    phone = clean_phone(payload.get("phone"))
    if phone is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, INVALID_PHONE)

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

    # Waits for approval by the platform team, who get a one-time review link.
    review_token, review_token_hash = new_review_token()
    tenant = Tenant(
        name=payload.get("name"),
        slug=payload.get("slug"),
        plan="free",
        workspace_id=payload.get("workspace_id"),
        seat_limit=15,
        owner_id=current_user.id,
        country=payload.get("country"),
        phone=phone,
        school_type=_short_answer(payload.get("type")),
        referral_source=_short_answer(payload.get("source")),
        status="pending",
        review_token_hash=review_token_hash,
    )
    owner_name = display_name(supabase_user.user_metadata, current_user.name)
    if owner_name and not current_user.name:
        current_user.name = owner_name  # so the review page and team list can show it
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

    # Tell the platform team; runs after the response, and a failure never affects signup.
    background_tasks.add_task(
        notify_school_registered,
        school_name=tenant.name,
        country=tenant.country,
        plan=payload.get("plan") or tenant.plan,
        workspace_id=tenant.workspace_id,
        owner_name=owner_name,
        owner_email=current_user.email,
        phone=tenant.phone,
        school_type=tenant.school_type,
        referral_source=tenant.referral_source,
        review_url=review_url(review_token),
    )
    # ...and thank the person who registered: it's pending, they'll hear back.
    background_tasks.add_task(
        notify_owner_school_received,
        school_name=tenant.name,
        owner_name=owner_name,
        owner_email=current_user.email,
    )

    return {
        "message": "signup successful",
        "tenant_id": str(tenant.id),
        "status": tenant.status,
    }


SCHOOL_PENDING = "This school is awaiting approval."


def ensure_school_approved(tenant_status: str) -> None:
    """Every school-scoped request goes through this: pending schools can't be used yet."""
    if tenant_status != "approved":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=SCHOOL_PENDING)


async def get_current_member(db: AsyncSession, current_user: User) -> TenantMember:
    """The caller's active membership, refused while its school awaits approval."""
    row = (
        await db.execute(
            select(TenantMember, Tenant.status)
            .join(Tenant, Tenant.id == TenantMember.tenant_id)
            .where(TenantMember.user_id == current_user.id, TenantMember.status == "active")
            .order_by(TenantMember.joined_at)
        )
    ).first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not belong to an active tenant",
        )

    member: TenantMember = row[0]
    ensure_school_approved(row[1])
    return member


async def get_current_tenant_id(
    db: AsyncSession,
    current_user: User,
) -> UUID:
    return (await get_current_member(db, current_user)).tenant_id
