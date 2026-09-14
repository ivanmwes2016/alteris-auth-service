from .academics import SchoolClass, Subject, SubjectClass, SubjectTeacher
from .attendance import StudentAttendanceProfile, StudentAttendanceRecord
from .behaviour import (
    BehaviourIncident,
    BehaviourNote,
    BehaviourRecognition,
    StudentBehaviourProfile,
)
from .enquiries import Enquiry
from .invite import Invite
from .medical_note import (
    StudentAllergy,
    StudentMedicalCondition,
    StudentMedicalProfile,
    StudentMedication,
)
from .parent import Parent
from .profile import Profile
from .role import Role
from .staff import Staff, StaffQualification, StaffQualificationProfile, StaffSubject
from .student import Student
from .student_parent import StudentParent
from .tenant import Tenant
from .tenant_member import TenantMember
from .tenant_profile import TenantProfile
from .tution_fees import TutionFees
from .users import User

__all__ = [
    "BehaviourIncident",
    "BehaviourNote",
    "BehaviourRecognition",
    "Enquiry",
    "Invite",
    "Parent",
    "Profile",
    "Role",
    "SchoolClass",
    "Staff",
    "StaffQualification",
    "StaffQualificationProfile",
    "StaffSubject",
    "Student",
    "StudentAllergy",
    "StudentAttendanceProfile",
    "StudentAttendanceRecord",
    "StudentBehaviourProfile",
    "StudentMedicalCondition",
    "StudentMedicalProfile",
    "StudentMedication",
    "StudentParent",
    "Subject",
    "SubjectClass",
    "SubjectTeacher",
    "Tenant",
    "TenantMember",
    "TenantProfile",
    "TutionFees",
    "User",
]
