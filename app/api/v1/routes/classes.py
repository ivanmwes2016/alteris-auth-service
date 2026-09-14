from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.auth import get_current_tenant_id, get_current_user
from app.core.db import get_db
from app.db.models.academics import SchoolClass, SubjectClass
from app.db.models.student import Student
from app.db.models.users import User

router = APIRouter()


class ClassResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    stream: str | None = None
    teacher: str | None = None
    students: int = 0
    subjects: int = 0


class ClassCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    stream: str | None = Field(None, max_length=100)
    teacher: str | None = Field(None, max_length=200)


class ClassUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    stream: str | None = Field(None, max_length=100)
    teacher: str | None = Field(None, max_length=200)


def _display_name(school_class: SchoolClass) -> str:
    """Mirrors the frontend's classDisplayName(): [name, stream] joined by a space."""
    return " ".join(part for part in (school_class.name, school_class.stream) if part)


async def _student_counts_by_class_name(db: AsyncSession, *, tenant_id: UUID) -> dict[str, int]:
    result = await db.execute(
        select(Student.class_applied, func.count(Student.id))
        .where(Student.tenant_id == tenant_id, Student.class_applied.is_not(None))
        .group_by(Student.class_applied)
    )
    counts: dict[str, int] = {}
    for name, count in result.all():
        counts[name] = count
    return counts


async def _subject_counts_by_class_display_name(
    db: AsyncSession, *, tenant_id: UUID
) -> dict[str, int]:
    result = await db.execute(
        select(SubjectClass.name, func.count(SubjectClass.id))
        .where(SubjectClass.tenant_id == tenant_id)
        .group_by(SubjectClass.name)
    )
    counts: dict[str, int] = {}
    for name, count in result.all():
        counts[name] = count
    return counts


def _to_response(
    school_class: SchoolClass, *, student_count: int, subject_count: int
) -> ClassResponse:
    return ClassResponse(
        id=school_class.id,
        name=school_class.name,
        stream=school_class.stream,
        teacher=school_class.teacher,
        students=student_count,
        subjects=subject_count,
    )


@router.get("", response_model=list[ClassResponse], status_code=status.HTTP_200_OK)
async def get_classes(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ClassResponse]:
    tenant_id = await get_current_tenant_id(db, current_user)

    result = await db.execute(
        select(SchoolClass).where(SchoolClass.tenant_id == tenant_id).order_by(SchoolClass.name)
    )
    school_classes = list(result.scalars().unique().all())

    student_counts = await _student_counts_by_class_name(db, tenant_id=tenant_id)
    subject_counts = await _subject_counts_by_class_display_name(db, tenant_id=tenant_id)

    return [
        _to_response(
            c,
            student_count=student_counts.get(c.name, 0),
            subject_count=subject_counts.get(_display_name(c), 0),
        )
        for c in school_classes
    ]


@router.get("/{class_id}", response_model=ClassResponse, status_code=status.HTTP_200_OK)
async def get_class(
    class_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ClassResponse:
    tenant_id = await get_current_tenant_id(db, current_user)

    result = await db.execute(
        select(SchoolClass).where(SchoolClass.id == class_id, SchoolClass.tenant_id == tenant_id)
    )
    school_class = result.scalar_one_or_none()

    if school_class is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")

    student_counts = await _student_counts_by_class_name(db, tenant_id=tenant_id)
    subject_counts = await _subject_counts_by_class_display_name(db, tenant_id=tenant_id)

    return _to_response(
        school_class,
        student_count=student_counts.get(school_class.name, 0),
        subject_count=subject_counts.get(_display_name(school_class), 0),
    )


@router.post("", response_model=ClassResponse, status_code=status.HTTP_201_CREATED)
async def create_class(
    payload: ClassCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ClassResponse:
    tenant_id = await get_current_tenant_id(db, current_user)

    school_class = SchoolClass(
        tenant_id=tenant_id,
        name=payload.name,
        stream=payload.stream,
        teacher=payload.teacher,
    )
    db.add(school_class)

    try:
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A class with this name and stream already exists",
        ) from exc

    return _to_response(school_class, student_count=0, subject_count=0)


@router.patch("/{class_id}", response_model=ClassResponse, status_code=status.HTTP_200_OK)
async def update_class(
    class_id: UUID,
    payload: ClassUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ClassResponse:
    tenant_id = await get_current_tenant_id(db, current_user)

    result = await db.execute(
        select(SchoolClass).where(SchoolClass.id == class_id, SchoolClass.tenant_id == tenant_id)
    )
    school_class = result.scalar_one_or_none()

    if school_class is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(school_class, field, value)

    try:
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A class with this name and stream already exists",
        ) from exc

    student_counts = await _student_counts_by_class_name(db, tenant_id=tenant_id)
    subject_counts = await _subject_counts_by_class_display_name(db, tenant_id=tenant_id)

    return _to_response(
        school_class,
        student_count=student_counts.get(school_class.name, 0),
        subject_count=subject_counts.get(_display_name(school_class), 0),
    )


@router.delete("/{class_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_class(
    class_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    tenant_id = await get_current_tenant_id(db, current_user)

    result = await db.execute(
        select(SchoolClass).where(SchoolClass.id == class_id, SchoolClass.tenant_id == tenant_id)
    )
    school_class = result.scalar_one_or_none()

    if school_class is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")

    try:
        await db.delete(school_class)
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete class",
        ) from exc
