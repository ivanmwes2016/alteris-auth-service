import uuid

from sqlalchemy import JSON, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class StudentMedicalNote(Base):
    __tablename__ = "student_medical_notes"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = mapped_column(UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"))
    tenant_id = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"))

    note = mapped_column(Text, nullable=False)
    health_conditions: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    allergies: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    medications: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    doctor: Mapped[str] = mapped_column(Text, nullable=True)

    created_at = mapped_column(DateTime, server_default=func.now())
    updated_at = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    student = relationship("Student", back_populates="medical_notes")
    tenant = relationship("Tenant")
