"""The authorization decision service. See docs/AUTHORIZATION.md for the
full model; this module is the implementation of that document.

Composition, not a role switch: `authorize()` always checks permission
first, then - for resource-scoped calls - a resource-relationship check
(`can_access_*`). Having the permission never implies access to every
resource of that type; see docs/AUTHORIZATION.md, "Permission + scope".
"""

import uuid
from collections.abc import Callable, Iterable
from enum import StrEnum

from sqlalchemy.orm import Session

from app.permissions.exceptions import PermissionDenied
from app.permissions.roles import Role
from app.permissions.scope import UserScope
from app.repositories import clinical_repository


def resolve_hospital_scope(scope: UserScope, requested_hospital_id: uuid.UUID | None) -> uuid.UUID:
    """The shared "which hospital is this operation actually scoped to"
    rule: SUPER_ADMIN may target any hospital by id (its cross-hospital
    bypass, same as every can_access_* check below); every other role is
    bound to their own scope.hospital_id and cannot override it - supplying
    a different one is a scope-escape attempt, not a convenience, and is
    rejected. Used wherever an admin operation accepts an optional
    hospital_id "for SUPER_ADMIN's benefit" - employee invitations
    (Phase 5) and document management (Phase 7) both call this rather than
    each re-deriving the same rule.
    """
    if requested_hospital_id is None:
        return scope.hospital_id
    if scope.role is Role.SUPER_ADMIN:
        return requested_hospital_id
    if requested_hospital_id != scope.hospital_id:
        raise PermissionDenied("hospital_id does not match the caller's authorized hospital")
    return requested_hospital_id


def has_permission(scope: UserScope, permission: str | Iterable[str]) -> bool:
    """Pure membership check against a scope already resolved from the
    database (see resolve_user_scope) - this function does no DB work
    itself, so callers checking several permissions against the same scope
    don't re-query for each one.

    `permission` may be a single code or an iterable of codes, in which case
    any one of them being present is sufficient (Phase 6 resources are often
    reachable via more than one seeded permission - e.g. a doctor via
    `view_assigned_patients` and a receptionist via
    `view_patient_basic_information` for the same `patients` endpoint). This
    never widens what a role can do - it only lets one endpoint accept
    whichever of several *already-seeded* permissions applies to the caller.
    """
    if isinstance(permission, str):
        return permission in scope.permissions
    return any(code in scope.permissions for code in permission)


# --- resource-relationship checks -----------------------------------------
#
# Each answers "does `scope`'s user have a relationship to this specific
# resource that authorizes access" - independent of whether they hold the
# relevant permission at all (that's has_permission's job; authorize()
# below composes both). None of these accept a resource's hospital/patient
# id as proof of anything - they look the real owner up in the database.


def can_access_patient(db: Session, scope: UserScope, patient_id: uuid.UUID) -> bool:
    hospital_id = clinical_repository.get_patient_hospital_id(db, patient_id)
    if hospital_id is None:
        return False  # no such patient
    if scope.role is Role.SUPER_ADMIN:
        return True
    if hospital_id != scope.hospital_id:
        return False
    if scope.role is Role.PATIENT:
        return scope.patient_id is not None and scope.patient_id == patient_id
    if scope.role is Role.DOCTOR:
        return scope.doctor_id is not None and clinical_repository.has_active_assignment(
            db, doctor_id=scope.doctor_id, patient_id=patient_id
        )
    # NURSE, RECEPTIONIST, STAFF, HOSPITAL_ADMIN: no patient-level clinical
    # relationship in this phase. NURSE in particular has no assignment
    # table to check against - see docs/AUTHORIZATION.md, "Limitations".
    return False


def can_access_appointment(db: Session, scope: UserScope, appointment_id: uuid.UUID) -> bool:
    ownership = clinical_repository.get_appointment_ownership(db, appointment_id)
    if ownership is None:
        return False
    if scope.role is Role.SUPER_ADMIN:
        return True
    if ownership.hospital_id != scope.hospital_id:
        return False
    if scope.role is Role.PATIENT:
        return scope.patient_id is not None and scope.patient_id == ownership.patient_id
    if scope.role is Role.DOCTOR:
        return scope.doctor_id is not None and clinical_repository.has_active_assignment(
            db, doctor_id=scope.doctor_id, patient_id=ownership.patient_id
        )
    if scope.role is Role.HOSPITAL_ADMIN:
        # Hospital-wide administrative/operational scope, not
        # department-limited - see docs/AUTHORIZATION.md, "Department scope".
        return True
    if scope.role in (Role.RECEPTIONIST, Role.STAFF):
        return scope.department_id is not None and scope.department_id == ownership.department_id
    return False  # NURSE: no operational appointment access assumed


def can_access_medical_record(db: Session, scope: UserScope, record_id: uuid.UUID) -> bool:
    ownership = clinical_repository.get_medical_record_ownership(db, record_id)
    if ownership is None:
        return False
    return can_access_patient(db, scope, ownership.patient_id)


def can_access_lab_report(db: Session, scope: UserScope, report_id: uuid.UUID) -> bool:
    ownership = clinical_repository.get_lab_report_ownership(db, report_id)
    if ownership is None:
        return False
    return can_access_patient(db, scope, ownership.patient_id)


def can_access_prescription(db: Session, scope: UserScope, prescription_id: uuid.UUID) -> bool:
    ownership = clinical_repository.get_prescription_ownership(db, prescription_id)
    if ownership is None:
        return False
    return can_access_patient(db, scope, ownership.patient_id)


def can_access_doctor(db: Session, scope: UserScope, doctor_id: uuid.UUID) -> bool:
    """Doctors/departments are hospital-scoped directory resources, not
    patient-relationship ones - no assignment/ownership concept applies,
    just the same hospital-isolation rule (and SUPER_ADMIN bypass) every
    other resource check uses."""
    hospital_id = clinical_repository.get_doctor_hospital_id(db, doctor_id)
    if hospital_id is None:
        return False
    return scope.role is Role.SUPER_ADMIN or hospital_id == scope.hospital_id


def can_access_department(db: Session, scope: UserScope, department_id: uuid.UUID) -> bool:
    hospital_id = clinical_repository.get_department_hospital_id(db, department_id)
    if hospital_id is None:
        return False
    return scope.role is Role.SUPER_ADMIN or hospital_id == scope.hospital_id


class ResourceKind(StrEnum):
    PATIENT = "patient"
    APPOINTMENT = "appointment"
    MEDICAL_RECORD = "medical_record"
    LAB_REPORT = "lab_report"
    PRESCRIPTION = "prescription"
    DOCTOR = "doctor"
    DEPARTMENT = "department"


_RESOURCE_CHECKS: dict[ResourceKind, Callable[[Session, UserScope, uuid.UUID], bool]] = {
    ResourceKind.PATIENT: can_access_patient,
    ResourceKind.APPOINTMENT: can_access_appointment,
    ResourceKind.MEDICAL_RECORD: can_access_medical_record,
    ResourceKind.LAB_REPORT: can_access_lab_report,
    ResourceKind.PRESCRIPTION: can_access_prescription,
    ResourceKind.DOCTOR: can_access_doctor,
    ResourceKind.DEPARTMENT: can_access_department,
}


def authorize(
    db: Session,
    scope: UserScope,
    permission: str | Iterable[str],
    *,
    resource: tuple[ResourceKind, uuid.UUID] | None = None,
) -> None:
    """Raises PermissionDenied unless `scope` holds `permission` (a single
    code, or - see has_permission - any one of several) and - when
    `resource` is given - is authorized to reach that specific resource.

    This is the one place these two checks are combined; callers (the
    require_permission FastAPI dependency, or a future service) should call
    this rather than calling has_permission()/can_access_*() separately and
    re-deriving the combination rule themselves.
    """
    if not has_permission(scope, permission):
        raise PermissionDenied(f"missing permission: {permission}")
    if resource is not None:
        kind, resource_id = resource
        if not _RESOURCE_CHECKS[kind](db, scope, resource_id):
            raise PermissionDenied(f"{kind.value} {resource_id} is outside the caller's authorized scope")
