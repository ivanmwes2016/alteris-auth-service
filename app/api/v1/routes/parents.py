from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, computed_field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.routes.auth import get_current_tenant_id, get_current_user
from app.core.db import get_db
from app.crud import parent_access, student_access
from app.db.models.parent import Parent
from app.db.models.student_parent import StudentParent
from app.db.models.users import User
from app.db.schemas.parent_access import AppStatus, ParentAppAccess
from app.helpers import rate_limit
from app.helpers.pagination import (
    Page,
    PageParams,
    fetch_page,
    make_page,
    page_params,
    search_filter,
)

router = APIRouter()


class StudentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str


class ParentStudentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    relationship_type: str | None = None
    is_primary_contact: bool
    can_pick_up: bool

    student: StudentSummary


class ParentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    photo: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    occupation: str | None = None
    students: list[ParentStudentResponse] = Field(default_factory=list)

    # App access (app.crud.parent_access)
    user_id: UUID | None = None
    invited_at: datetime | None = None
    invite_expires_at: datetime | None = None
    joined_at: datetime | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def app_status(self) -> AppStatus:
        return parent_access.app_status(
            user_id=self.user_id, invite_expires_at=self.invite_expires_at, now=datetime.now(UTC)
        )


class ParentPatch(BaseModel):
    name: str | None = None
    photo: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    occupation: str | None = None


class ParentStudentPatch(BaseModel):
    relationship_type: str | None = None
    is_primary_contact: bool | None = None
    can_pick_up: bool | None = None


# Get all parents for the current user's tenant
@router.get(
    "",
    response_model=list[ParentResponse] | Page[ParentResponse],
    status_code=status.HTTP_200_OK,
)
async def get_parents(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    params: PageParams = Depends(page_params),
) -> list[ParentResponse] | Page[ParentResponse]:
    """All parents, or one page of them with `?page=` (search: name, email or phone)."""
    tenant_id = await get_current_tenant_id(db, current_user)

    query = (
        select(Parent)
        .where(Parent.tenant_id == tenant_id)
        .options(selectinload(Parent.students).selectinload(StudentParent.student))
        .order_by(Parent.name, Parent.id)
    )
    matches = search_filter(params.search, Parent.name, Parent.email, Parent.phone)
    if matches is not None:
        query = query.where(matches)

    if params.page is None:
        result = await db.execute(query)
        return [ParentResponse.model_validate(p) for p in result.scalars().unique().all()]

    parents, total = await fetch_page(db, query, params)
    return make_page([ParentResponse.model_validate(p) for p in parents], total, params)


@router.get(
    "/{parent_id}",
    response_model=ParentResponse,
    status_code=status.HTTP_200_OK,
)
async def get_parent(
    parent_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Parent:
    tenant_id = await get_current_tenant_id(db, current_user)

    result = await db.execute(
        select(Parent)
        .where(
            Parent.id == parent_id,
            Parent.tenant_id == tenant_id,
        )
        .options(selectinload(Parent.students).selectinload(StudentParent.student))
    )

    parent = result.scalar_one_or_none()

    if parent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Parent not found",
        )

    return parent


@router.patch(
    "/{parent_id}",
    response_model=ParentResponse,
    status_code=status.HTTP_200_OK,
)
async def update_parent(
    parent_id: UUID,
    payload: ParentPatch,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Parent:
    tenant_id = await get_current_tenant_id(db, current_user)

    result = await db.execute(
        select(Parent).where(
            Parent.id == parent_id,
            Parent.tenant_id == tenant_id,
        )
    )

    parent = result.scalar_one_or_none()

    if parent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Parent not found",
        )

    update_data = payload.model_dump(exclude_unset=True)
    if "email" in update_data:
        new_email = (update_data["email"] or "").strip().lower() or None
        update_data["email"] = new_email
        if not parent_access.same_email(parent.email, new_email):
            # The login and any invite belong to the old address.
            parent_access.clear_access(parent)

    for field, value in update_data.items():
        setattr(parent, field, value)

    try:
        await db.commit()
        await db.refresh(parent)

        refreshed_result = await db.execute(
            select(Parent)
            .where(
                Parent.id == parent_id,
                Parent.tenant_id == tenant_id,
            )
            .options(selectinload(Parent.students).selectinload(StudentParent.student))
        )

        updated_parent = refreshed_result.scalar_one()

        return updated_parent

    except SQLAlchemyError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update parent",
        ) from exc


@router.patch(
    "/{parent_id}/students/{student_id}",
    response_model=ParentStudentResponse,
)
async def update_parent_student_relationship(
    parent_id: UUID,
    student_id: UUID,
    payload: ParentStudentPatch,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StudentParent:
    tenant_id = await get_current_tenant_id(db, current_user)

    result = await db.execute(
        select(StudentParent)
        .join(Parent, StudentParent.parent_id == Parent.id)
        .where(
            StudentParent.parent_id == parent_id,
            StudentParent.student_id == student_id,
            Parent.tenant_id == tenant_id,
        )
        .options(selectinload(StudentParent.student))
    )

    relationship = result.scalar_one_or_none()

    if relationship is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Parent-student relationship not found",
        )

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(relationship, field, value)

    try:
        await db.commit()

        refreshed_result = await db.execute(
            select(StudentParent)
            .where(
                StudentParent.parent_id == relationship.parent_id,
                StudentParent.student_id == relationship.student_id,
            )
            .options(selectinload(StudentParent.student))
        )

        return refreshed_result.scalar_one()

    except SQLAlchemyError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update parent-student relationship",
        ) from exc


# Remove Relationship: This should delete only the link, not the student or parent
@router.delete(
    "/{parent_id}/students/{student_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_student_from_parent(
    parent_id: UUID,
    student_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    tenant_id = await get_current_tenant_id(db, current_user)

    result = await db.execute(
        select(StudentParent)
        .join(Parent, StudentParent.parent_id == Parent.id)
        .where(
            StudentParent.parent_id == parent_id,
            StudentParent.student_id == student_id,
            Parent.tenant_id == tenant_id,
        )
    )

    relationship = result.scalar_one_or_none()

    if relationship is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Parent-student relationship not found",
        )

    try:
        await db.delete(relationship)
        await db.commit()

    except SQLAlchemyError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to remove parent from student",
        ) from exc


# ---------------------------------------------------------------------------
# App access: inviting a parent to follow their children in the app
# ---------------------------------------------------------------------------
@router.post("/{parent_id}/invite", response_model=ParentAppAccess)
async def invite_parent(
    parent_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ParentAppAccess:
    """Email the parent a link to set up the app. Sending again replaces the link."""
    caller = await student_access.require(db, current_user, "edit_details")
    await rate_limit.enforce("invite_user", str(current_user.id))
    await rate_limit.enforce("invite_tenant", str(caller.tenant_id))
    return await parent_access.invite(db, caller, parent_id)


@router.delete("/{parent_id}/invite", response_model=ParentAppAccess)
async def remove_parent_access(
    parent_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ParentAppAccess:
    """End the parent's app access, or cancel their invite. Their record stays."""
    caller = await student_access.require(db, current_user, "edit_details")
    return await parent_access.revoke(db, caller, parent_id)
