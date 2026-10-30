from .academic_term import AcademicTermDates, AcademicTermSettings
from .academics import SchoolClass, Subject, SubjectClass, SubjectTeacher
from .attendance import (
    AttendanceMark,
    AttendanceMarkChange,
    AttendanceRegister,
    CorrectionRequest,
    CorrectionRequestItem,
    NoSchoolDay,
    StudentAttendanceProfile,
    StudentAttendanceRecord,
)
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
from .performance import PerformanceRecord
from .planning import (
    LessonPlan,
    LessonPlanVersion,
    PlannedLesson,
    PlanningAttachment,
    PlanningComment,
    SchemeEntry,
    SchemeOfWork,
)
from .profile import Profile
from .push_token import PushToken
from .role import Role
from .staff import (
    Staff,
    StaffAllergy,
    StaffDocument,
    StaffHealthCondition,
    StaffHealthProfile,
    StaffQualification,
    StaffQualificationProfile,
    StaffSubject,
)
from .student import Student, StudentSubject, StudentSubjectChange
from .student_parent import StudentParent
from .tenant import Tenant
from .tenant_gallery_image import TenantGalleryImage
from .tenant_member import TenantMember
from .tenant_profile import TenantProfile
from .timetable import Lesson, OptionsPeriod, Timetable, TimetablePeriod
from .tution_fees import TutionFees
from .users import User

__all__ = [
    "AcademicTermDates",
    "AcademicTermSettings",
    "AttendanceMark",
    "AttendanceMarkChange",
    "AttendanceRegister",
    "BehaviourIncident",
    "BehaviourNote",
    "BehaviourRecognition",
    "CorrectionRequest",
    "CorrectionRequestItem",
    "Enquiry",
    "Invite",
    "Lesson",
    "LessonPlan",
    "LessonPlanVersion",
    "NoSchoolDay",
    "OptionsPeriod",
    "Parent",
    "PerformanceRecord",
    "PlannedLesson",
    "PlanningAttachment",
    "PlanningComment",
    "Profile",
    "PushToken",
    "Role",
    "SchemeEntry",
    "SchemeOfWork",
    "SchoolClass",
    "Staff",
    "StaffAllergy",
    "StaffDocument",
    "StaffHealthCondition",
    "StaffHealthProfile",
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
    "StudentSubject",
    "StudentSubjectChange",
    "Subject",
    "SubjectClass",
    "SubjectTeacher",
    "Tenant",
    "TenantGalleryImage",
    "TenantMember",
    "TenantProfile",
    "Timetable",
    "TimetablePeriod",
    "TutionFees",
    "User",
]
