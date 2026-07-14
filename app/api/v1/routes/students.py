from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.routes.auth import get_current_user
from app.core.db import get_db
from app.db.models.medical_note import StudentMedicalNote
from app.db.models.parent import Parent
from app.db.models.student import Student
from app.db.models.student_parent import StudentParent
from app.db.models.tenant_member import TenantMember
from app.db.models.users import User

router = APIRouter()


class MedicalNoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    blood_group: str | None = None
    doctor_name: str | None = None
    doctor_phone: str | None = None
    allergies: list[str] = []
    medications: list[str] = []
    notes: str | None = None


class StudentParentLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    parent_id: UUID
    relationship_type: str
    is_primary_contact: bool
    can_pick_up: bool


class AttendanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    date: date
    status: str
    notes: str | None = None


class StudentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    school_id: str | None

    name: str
    status: str | None = None
    dob: date | None = None
    gender: str | None = None
    photo: str | None = None
    address: str | None = None
    email: str | None = None
    phone: str | None = None
    class_: str | None = Field(
        default=None, validation_alias="class_applied", serialization_alias="class"
    )
    faculty: str | None = None
    course: str | None = None

    created_at: datetime
    updated_at: datetime | None = None
    study_format: str | None = None

    medicalNotes: list[MedicalNoteResponse] = []  # noqa: N815
    parents: list[StudentParentLinkResponse] = []
    attendanceRecords: list[AttendanceResponse] = []  # noqa: N815


class ParentCreate(BaseModel):
    first_name: str
    last_name: str
    email: str | None = None
    phone: str | None = None
    occupation: str | None = None
    address: str | None = None

    relationship_type: str
    is_primary_contact: bool = False
    can_pick_up: bool = True


class MedicalCreate(BaseModel):
    blood_group: str | None = None
    doctor_name: str | None = None
    doctor_phone: str | None = None
    allergies: list[str] = []
    medications: list[str] = []
    notes: str | None = None


class StudentCreate(BaseModel):
    tenant_id: str
    name: str | None
    status: str | None = "active"
    date_of_birth: date | None = None
    gender: str | None = None
    photo: str | None = None
    school_id: str | None = None
    class_applied: str | None = None
    faculty: str | None = None
    course: str | None = None
    parents: list[ParentCreate] = []
    medical: MedicalCreate | None = None
    attendance: list[AttendanceResponse] = []
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    study_format: str | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_student(
    payload: StudentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str]:
    try:
        member_result = await db.execute(
            select(TenantMember).where(TenantMember.user_id == current_user.id)
        )
        member = member_result.scalar_one_or_none()

        if not member:
            raise HTTPException(status_code=403, detail="User does not belong to a school")

        student = Student(
            tenant_id=payload.tenant_id,
            school_id=payload.school_id or "",
            name=payload.name,
            status=payload.status,
            date_of_birth=payload.date_of_birth,
            gender=payload.gender,
            photo=payload.photo,
            address=payload.address,
            email=payload.email,
            phone=payload.phone,
            class_applied=payload.class_applied,
            faculty=payload.faculty,
            course=payload.course,
        )

        db.add(student)
        await db.flush()

        parents_objs = [
            Parent(
                tenant_id=payload.tenant_id,
                first_name=p.first_name,
                last_name=p.last_name,
                email=p.email,
                phone=p.phone,
                occupation=p.occupation,
                address=p.address,
            )
            for p in payload.parents
        ]

        db.add_all(parents_objs)
        await db.flush()

        for parent_data, parent in zip(payload.parents, parents_objs, strict=False):
            db.add(
                StudentParent(
                    student_id=student.id,
                    parent_id=parent.id,
                    relationship_type=parent_data.relationship_type,
                    is_primary_contact=parent_data.is_primary_contact,
                    can_pick_up=parent_data.can_pick_up,
                )
            )

        if payload.medical:
            medical = StudentMedicalNote(
                tenant_id=payload.tenant_id,
                student_id=student.id,
                blood_group=payload.medical.blood_group,
                doctor_name=payload.medical.doctor_name,
                doctor_phone=payload.medical.doctor_phone,
                allergies=payload.medical.allergies,
                medications=payload.medical.medications,
                notes=payload.medical.notes,
            )

            db.add(medical)

        await db.commit()
        await db.refresh(student)

        return {
            "message": "Student created successfully",
        }

    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get("", response_model=list[StudentResponse], status_code=status.HTTP_200_OK)
async def get_students(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[StudentResponse]:
    member_result = await db.execute(
        select(TenantMember).where(TenantMember.user_id == current_user.id)
    )
    member = member_result.scalar_one_or_none()

    if not member:
        raise HTTPException(status_code=403, detail="User does not belong to a school")

    result = await db.execute(
        select(Student)
        .where(Student.tenant_id == member.tenant_id)
        .options(
            selectinload(Student.medical_notes),
            selectinload(Student.parents),
            selectinload(Student.attendance_records),
        )
    )

    students = result.scalars().all()

    return [
        {
            **student.__dict__,
            "medicalNotes": student.medical_notes,
            "attendanceRecords": student.attendance_records,
            "dob": student.date_of_birth,
            "admissionNo": student.school_id,
        }
        for student in students
    ]


@router.get(
    "/{student_id}",
    response_model=StudentResponse,
    status_code=status.HTTP_200_OK,
)
async def get_student_by_id(
    student_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> StudentResponse:
    member_result = await db.execute(
        select(TenantMember).where(TenantMember.user_id == current_user.id)
    )
    member = member_result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=403,
            detail="User does not belong to a school",
        )

    result = await db.execute(
        select(Student)
        .where(
            Student.id == student_id,
            Student.tenant_id == member.tenant_id,
        )
        .options(
            selectinload(Student.medical_notes),
            selectinload(Student.parents),
            selectinload(Student.attendance_records),
        )
    )

    student = result.scalar_one_or_none()

    if not student:
        raise HTTPException(
            status_code=404,
            detail="Student not found",
        )

    return {
        **student.__dict__,
        "medicalNotes": student.medical_notes,
        "attendanceRecords": student.attendance_records,
        "dob": student.date_of_birth,
    }


@router.patch("/{student_id}", status_code=status.HTTP_201_CREATED)
async def update_student(
    student_id: UUID,
    payload: StudentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str]:
    try:
        member_result = await db.execute(
            select(TenantMember).where(TenantMember.user_id == current_user.id)
        )
        member = member_result.scalar_one_or_none()

        if not member:
            raise HTTPException(status_code=403, detail="User does not belong to a school")

        student_result = await db.execute(
            select(Student).where(
                Student.id == student_id,
                Student.tenant_id == member.tenant_id,
            )
        )

        student = student_result.scalar_one_or_none()

        if not student:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Student not found",
            )

        update_data = payload.model_dump(exclude_unset=True, exclude={"tenant_id", "id"})

        # Update student fields
        for field, value in update_data.items():
            setattr(student, field, value)

        await db.commit()
        await db.refresh(student)

        return {
            "message": "Student updated successfully",
        }

    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
