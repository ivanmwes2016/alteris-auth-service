from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.routes.auth import get_current_tenant_id, get_current_user
from app.core.db import get_db
from app.db.models.parent import Parent
from app.db.models.student_parent import StudentParent
from app.db.models.users import User

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
    first_name: str
    last_name: str
    photo: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    occupation: str | None = None
    students: list[ParentStudentResponse] = Field(default_factory=list)


class ParentPatch(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
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
    response_model=list[ParentResponse],
    status_code=status.HTTP_200_OK,
)
async def get_parents(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Parent]:
    tenant_id = await get_current_tenant_id(db, current_user)

    result = await db.execute(
        select(Parent)
        .where(Parent.tenant_id == tenant_id)
        .options(selectinload(Parent.students).selectinload(StudentParent.student))
        .order_by(Parent.first_name, Parent.last_name)
    )

    return list(result.scalars().unique().all())


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
            .where(StudentParent.id == relationship.id)
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
