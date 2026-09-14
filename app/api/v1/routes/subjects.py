from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.routes.auth import get_current_tenant_id, get_current_user
from app.core.db import get_db
from app.db.models.academics import ClassSubject, SchoolClass, Subject, SubjectTeacher
from app.db.models.users import User

router = APIRouter()


class SubjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    code: str
    teachers: list[str] = Field(default_factory=list)
    classes: list[str] = Field(default_factory=list)


class SubjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    code: str = Field(..., min_length=1, max_length=50)
    teachers: list[str] = Field(default_factory=list)
    class_ids: list[UUID] = Field(default_factory=list)


class SubjectUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    code: str | None = Field(None, min_length=1, max_length=50)
    teachers: list[str] | None = None
    class_ids: list[UUID] | None = None


def _to_response(subject: Subject) -> SubjectResponse:
    return SubjectResponse(
        id=subject.id,
        name=subject.name,
        code=subject.code,
        teachers=[t.name for t in subject.teachers],
        classes=[link.school_class.name for link in subject.class_links],
    )


async def _set_teachers(
    db: AsyncSession, *, subject: Subject, teachers: list[str], tenant_id: UUID
) -> None:
    # Avoid touching `subject.teachers` directly: on a just-flushed object it
    # isn't loaded yet, and reading/assigning it here would trigger an
    # implicit lazy-load, which AsyncSession does not support.
    await db.execute(delete(SubjectTeacher).where(SubjectTeacher.subject_id == subject.id))

    db.add_all(
        SubjectTeacher(subject_id=subject.id, name=name, tenant_id=tenant_id)
        for name in teachers
        if name.strip()
    )


async def _set_class_links(
    db: AsyncSession,
    *,
    subject: Subject,
    class_ids: list[UUID],
    tenant_id: UUID,
) -> None:
    if class_ids:
        result = await db.execute(
            select(SchoolClass.id).where(
                SchoolClass.id.in_(class_ids), SchoolClass.tenant_id == tenant_id
            )
        )
        found_ids = set(result.scalars().all())
        missing = set(class_ids) - found_ids
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown class_ids: {sorted(str(i) for i in missing)}",
            )

    # Avoid touching `subject.class_links` directly: on a just-flushed object
    # it isn't loaded yet, and reading/assigning it here would trigger an
    # implicit lazy-load, which AsyncSession does not support.
    await db.execute(delete(ClassSubject).where(ClassSubject.subject_id == subject.id))

    db.add_all(
        ClassSubject(class_id=class_id, subject_id=subject.id, tenant_id=tenant_id)
        for class_id in class_ids
    )


def _eager_options() -> tuple[Any, ...]:
    return (
        selectinload(Subject.teachers),
        selectinload(Subject.class_links).selectinload(ClassSubject.school_class),
    )


@router.get("", response_model=list[SubjectResponse], status_code=status.HTTP_200_OK)
async def get_subjects(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[SubjectResponse]:
    tenant_id = await get_current_tenant_id(db, current_user)

    result = await db.execute(
        select(Subject)
        .where(Subject.tenant_id == tenant_id)
        .options(*_eager_options())
        .order_by(Subject.name)
    )
    subjects = result.scalars().unique().all()

    return [_to_response(s) for s in subjects]


@router.get("/{subject_id}", response_model=SubjectResponse, status_code=status.HTTP_200_OK)
async def get_subject(
    subject_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SubjectResponse:
    tenant_id = await get_current_tenant_id(db, current_user)

    result = await db.execute(
        select(Subject)
        .where(Subject.id == subject_id, Subject.tenant_id == tenant_id)
        .options(*_eager_options())
    )
    subject = result.scalar_one_or_none()

    if subject is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subject not found")

    return _to_response(subject)


@router.post("", response_model=SubjectResponse, status_code=status.HTTP_201_CREATED)
async def create_subject(
    payload: SubjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SubjectResponse:
    tenant_id = await get_current_tenant_id(db, current_user)

    subject = Subject(tenant_id=tenant_id, name=payload.name, code=payload.code)
    db.add(subject)
    await db.flush()

    await _set_teachers(db, subject=subject, teachers=payload.teachers, tenant_id=tenant_id)
    await _set_class_links(db, subject=subject, class_ids=payload.class_ids, tenant_id=tenant_id)

    try:
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A subject with this code already exists",
        ) from exc

    result = await db.execute(
        select(Subject).where(Subject.id == subject.id).options(*_eager_options())
    )
    subject = result.scalar_one()

    return _to_response(subject)


@router.patch("/{subject_id}", response_model=SubjectResponse, status_code=status.HTTP_200_OK)
async def update_subject(
    subject_id: UUID,
    payload: SubjectUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SubjectResponse:
    tenant_id = await get_current_tenant_id(db, current_user)

    result = await db.execute(
        select(Subject)
        .where(Subject.id == subject_id, Subject.tenant_id == tenant_id)
        .options(*_eager_options())
    )
    subject = result.scalar_one_or_none()

    if subject is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subject not found")

    update_data = payload.model_dump(exclude_unset=True, exclude={"teachers", "class_ids"})
    for field, value in update_data.items():
        setattr(subject, field, value)

    if payload.teachers is not None:
        await _set_teachers(db, subject=subject, teachers=payload.teachers, tenant_id=tenant_id)

    if payload.class_ids is not None:
        await _set_class_links(
            db, subject=subject, class_ids=payload.class_ids, tenant_id=tenant_id
        )

    try:
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A subject with this code already exists",
        ) from exc

    result = await db.execute(
        select(Subject).where(Subject.id == subject_id).options(*_eager_options())
    )
    subject = result.scalar_one()

    return _to_response(subject)


@router.delete("/{subject_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subject(
    subject_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    tenant_id = await get_current_tenant_id(db, current_user)

    result = await db.execute(
        select(Subject).where(Subject.id == subject_id, Subject.tenant_id == tenant_id)
    )
    subject = result.scalar_one_or_none()

    if subject is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subject not found")

    try:
        await db.delete(subject)
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete subject",
        ) from exc
