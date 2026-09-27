import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.permissions.roles import Role
from app.repositories import user_repository
from app.schemas.auth import AuthenticatedUser


@dataclass(frozen=True)
class UserScope:
    """A user's authorization scope, resolved entirely from the database for
    this one request - never accepted from the client. `permissions` is the
    live result of joining role_permissions/permissions for `role`; it is
    not a hard-coded per-role map.

    Not every field applies to every role (see docs/AUTHORIZATION.md,
    "Role-specific scope"): `patient_id` is only set for PATIENT,
    `doctor_id` only for DOCTOR, `staff_id` only for NURSE/RECEPTIONIST/
    STAFF, and department_id is whatever the user's own row has (may be
    None, e.g. for SUPER_ADMIN).
    """

    user_id: uuid.UUID
    auth_user_id: uuid.UUID
    role: Role
    permissions: frozenset[str]
    hospital_id: uuid.UUID
    department_id: uuid.UUID | None
    patient_id: uuid.UUID | None
    doctor_id: uuid.UUID | None
    staff_id: uuid.UUID | None


_STAFF_ROLES = frozenset({Role.NURSE, Role.RECEPTIONIST, Role.STAFF})


def resolve_user_scope(db: Session, user: AuthenticatedUser) -> UserScope:
    """Builds the authorization scope for an already-authenticated,
    already-active user (see app/auth/dependencies.get_current_auth_user).
    """
    role = Role(user.role)
    permissions = user_repository.get_role_permission_codes(db, role.value)

    patient_id = user_repository.get_patient_id_for_user(db, user.id) if role is Role.PATIENT else None
    doctor_id = user_repository.get_doctor_id_for_user(db, user.id) if role is Role.DOCTOR else None
    staff_id = user_repository.get_staff_id_for_user(db, user.id) if role in _STAFF_ROLES else None

    return UserScope(
        user_id=user.id,
        auth_user_id=user.auth_user_id,
        role=role,
        permissions=permissions,
        hospital_id=user.hospital_id,
        department_id=user.department_id,
        patient_id=patient_id,
        doctor_id=doctor_id,
        staff_id=staff_id,
    )
