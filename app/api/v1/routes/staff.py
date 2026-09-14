from datetime import date as date_
from enum import Enum
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.routes.auth import get_current_tenant_id, get_current_user
from app.core.db import get_db
from app.db.models.staff import Staff, StaffSubject
from app.db.models.users import User

router = APIRouter()


# ---------------------------------------------------------------------------
# Enums — mirrors ROLE_OPTIONS/RANK_OPTIONS_BY_ROLE/STATUS_OPTIONS on the frontend
# ---------------------------------------------------------------------------
class StaffRole(str, Enum):
    teacher = "Teacher"
    nurse = "Nurse"
    cleaner = "Cleaner"
    chef = "Chef"
    administrator = "Administrator"
    librarian = "Librarian"
    counselor = "Counselor"
    security_guard = "Security Guard"
    groundskeeper = "Groundskeeper"
    maintenance_technician = "Maintenance Technician"
    teaching_assistant = "Teaching Assistant"
    receptionist = "Receptionist"
    it_support = "IT Support"
    bus_driver = "Bus Driver"


class StaffRank(str, Enum):
    entry = "Entry"
    basic = "Basic"
    senior = "Senior"
    class_teacher = "Class Teacher"


class StaffStatus(str, Enum):
    active = "Active"
    on_leave = "On Leave"
    suspended = "Suspended"
    inactive = "Inactive"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class StaffResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str | None = None
    firstName: str = Field(validation_alias="first_name")  # noqa: N815
    lastName: str = Field(validation_alias="last_name")  # noqa: N815
    email: str | None = None
    phone: str | None = None
    gender: str | None = None
    dateJoined: date_ | None = Field(default=None, validation_alias="date_joined")  # noqa: N815
    status: StaffStatus
    role: StaffRole
    rank: StaffRank
    classAssigned: str | None = Field(  # noqa: N815
        default=None, validation_alias="class_assigned"
    )
    subjects: list[str] = Field(default_factory=list)


class StaffCreate(BaseModel):
    title: str | None = None
    first_name: str = Field(..., min_length=1, max_length=200)
    last_name: str = Field(..., min_length=1, max_length=200)
    email: str | None = None
    phone: str | None = None
    gender: str | None = None
    date_joined: date_ | None = None
    status: StaffStatus = StaffStatus.active
    role: StaffRole
    rank: StaffRank = StaffRank.basic
    class_assigned: str | None = None
    subjects: list[str] = Field(default_factory=list)

    @field_validator("date_joined", mode="before")
    @classmethod
    def _blank_date_to_none(cls, v: object) -> object:
        return None if v == "" else v


class StaffUpdate(BaseModel):
    title: str | None = None
    first_name: str | None = Field(None, min_length=1, max_length=200)
    last_name: str | None = Field(None, min_length=1, max_length=200)
    email: str | None = None
    phone: str | None = None
    gender: str | None = None
    date_joined: date_ | None = None
    status: StaffStatus | None = None
    role: StaffRole | None = None
    rank: StaffRank | None = None
    class_assigned: str | None = None
    subjects: list[str] | None = None

    @field_validator("date_joined", mode="before")
    @classmethod
    def _blank_date_to_none(cls, v: object) -> object:
        return None if v == "" else v


def _to_response(staff: Staff) -> StaffResponse:
    return StaffResponse.model_validate(
        {**staff.__dict__, "subjects": [s.name for s in staff.subjects]}
    )


async def _set_subjects(
    db: AsyncSession, *, staff: Staff, subjects: list[str], tenant_id: UUID
) -> None:
    # Avoid touching `staff.subjects` directly: on a just-flushed object it
    # isn't loaded yet, and reading/assigning it here would trigger an
    # implicit lazy-load, which AsyncSession does not support.
    await db.execute(delete(StaffSubject).where(StaffSubject.staff_id == staff.id))

    db.add_all(
        StaffSubject(staff_id=staff.id, name=name, tenant_id=tenant_id)
        for name in subjects
        if name.strip()
    )


@router.get("", response_model=list[StaffResponse], status_code=status.HTTP_200_OK)
async def get_staff_members(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[StaffResponse]:
    tenant_id = await get_current_tenant_id(db, current_user)

    result = await db.execute(
        select(Staff)
        .where(Staff.tenant_id == tenant_id)
        .options(selectinload(Staff.subjects))
        .order_by(Staff.first_name, Staff.last_name)
    )
    staff_members = result.scalars().unique().all()

    return [_to_response(s) for s in staff_members]


@router.get("/{staff_id}", response_model=StaffResponse, status_code=status.HTTP_200_OK)
async def get_staff_member(
    staff_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StaffResponse:
    tenant_id = await get_current_tenant_id(db, current_user)

    result = await db.execute(
        select(Staff)
        .where(Staff.id == staff_id, Staff.tenant_id == tenant_id)
        .options(selectinload(Staff.subjects))
    )
    staff = result.scalar_one_or_none()

    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff member not found")

    return _to_response(staff)


@router.post("", response_model=StaffResponse, status_code=status.HTTP_201_CREATED)
async def create_staff_member(
    payload: StaffCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StaffResponse:
    tenant_id = await get_current_tenant_id(db, current_user)

    staff = Staff(
        tenant_id=tenant_id,
        title=payload.title or None,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email or None,
        phone=payload.phone or None,
        gender=payload.gender or None,
        date_joined=payload.date_joined,
        status=payload.status.value,
        role=payload.role.value,
        rank=payload.rank.value,
        class_assigned=payload.class_assigned or None,
    )
    db.add(staff)
    await db.flush()

    await _set_subjects(db, staff=staff, subjects=payload.subjects, tenant_id=tenant_id)

    try:
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create staff member",
        ) from exc

    result = await db.execute(
        select(Staff).where(Staff.id == staff.id).options(selectinload(Staff.subjects))
    )
    staff = result.scalar_one()

    return _to_response(staff)


@router.patch("/{staff_id}", response_model=StaffResponse, status_code=status.HTTP_200_OK)
async def update_staff_member(
    staff_id: UUID,
    payload: StaffUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StaffResponse:
    tenant_id = await get_current_tenant_id(db, current_user)

    result = await db.execute(
        select(Staff)
        .where(Staff.id == staff_id, Staff.tenant_id == tenant_id)
        .options(selectinload(Staff.subjects))
    )
    staff = result.scalar_one_or_none()

    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff member not found")

    update_data = payload.model_dump(exclude_unset=True, exclude={"subjects"})
    for field, value in update_data.items():
        setattr(staff, field, value.value if isinstance(value, Enum) else value)

    if payload.subjects is not None:
        await _set_subjects(db, staff=staff, subjects=payload.subjects, tenant_id=tenant_id)

    try:
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update staff member",
        ) from exc

    result = await db.execute(
        select(Staff).where(Staff.id == staff_id).options(selectinload(Staff.subjects))
    )
    staff = result.scalar_one()

    return _to_response(staff)


@router.delete("/{staff_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_staff_member(
    staff_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    tenant_id = await get_current_tenant_id(db, current_user)

    result = await db.execute(
        select(Staff).where(Staff.id == staff_id, Staff.tenant_id == tenant_id)
    )
    staff = result.scalar_one_or_none()

    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff member not found")

    try:
        await db.delete(staff)
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete staff member",
        ) from exc
