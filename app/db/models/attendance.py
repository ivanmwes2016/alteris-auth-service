import uuid

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import mapped_column, relationship

from app.core.db import Base


class StudentAttendance(Base):
    __tablename__ = "student_attendance"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = mapped_column(UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"))
    tenant_id = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"))

    date = mapped_column(DateTime, nullable=False)
    status = mapped_column(String, nullable=False)
    # present, absent, late, excused

    reason = mapped_column(String, nullable=True)

    student = relationship("Student", back_populates="attendance_records")
    tenant = relationship("Tenant")
