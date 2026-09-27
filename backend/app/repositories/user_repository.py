import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.permissions.roles import Role

_SELECT_USER_WITH_ROLE = """
    SELECT u.id, u.auth_user_id, u.email, u.first_name, u.last_name,
           u.hospital_id, r.name AS role, u.department_id, u.is_active
    FROM users u
    JOIN roles r ON r.id = u.role_id
    WHERE {where}
"""


@dataclass(frozen=True)
class UserRecord:
    id: uuid.UUID
    auth_user_id: uuid.UUID
    email: str
    first_name: str
    last_name: str
    hospital_id: uuid.UUID
    role: str
    department_id: uuid.UUID | None
    is_active: bool


def get_user_by_auth_id(session: Session, auth_user_id: uuid.UUID | str) -> UserRecord | None:
    row = (
        session.execute(
            text(_SELECT_USER_WITH_ROLE.format(where="u.auth_user_id = :auth_user_id")),
            {"auth_user_id": str(auth_user_id)},
        )
        .mappings()
        .first()
    )
    return UserRecord(**row) if row else None


def get_role_permission_codes(session: Session, role_name: str) -> frozenset[str]:
    """The authoritative source of a role's permissions: always a live query
    against role_permissions/permissions, never a hard-coded Python map.
    """
    rows = session.execute(
        text(
            """
            SELECT p.code
            FROM role_permissions rp
            JOIN roles r ON r.id = rp.role_id
            JOIN permissions p ON p.id = rp.permission_id
            WHERE r.name = :role_name
            """
        ),
        {"role_name": role_name},
    ).scalars()
    return frozenset(rows)


def get_patient_id_for_user(session: Session, user_id: uuid.UUID) -> uuid.UUID | None:
    return session.execute(
        text("SELECT id FROM patients WHERE user_id = :user_id"), {"user_id": user_id}
    ).scalar_one_or_none()


def get_doctor_id_for_user(session: Session, user_id: uuid.UUID) -> uuid.UUID | None:
    return session.execute(
        text("SELECT id FROM doctors WHERE user_id = :user_id"), {"user_id": user_id}
    ).scalar_one_or_none()


def get_staff_id_for_user(session: Session, user_id: uuid.UUID) -> uuid.UUID | None:
    return session.execute(
        text("SELECT id FROM staff WHERE user_id = :user_id"), {"user_id": user_id}
    ).scalar_one_or_none()


def email_in_use(session: Session, email: str) -> bool:
    return (
        session.execute(
            text("SELECT 1 FROM users WHERE lower(email) = lower(:email)"),
            {"email": email},
        ).first()
        is not None
    )


def get_default_patient_hospital_id(session: Session, hospital_code: str) -> uuid.UUID:
    hospital_id = session.execute(
        text("SELECT id FROM hospitals WHERE code = :code AND is_active"),
        {"code": hospital_code},
    ).scalar_one_or_none()
    if hospital_id is None:
        raise LookupError(
            f"No active hospital with code {hospital_code!r} configured for patient registration."
        )
    return hospital_id


def create_patient_registration(
    session: Session,
    *,
    auth_user_id: uuid.UUID,
    email: str,
    first_name: str,
    last_name: str,
    hospital_id: uuid.UUID,
) -> UserRecord:
    """Creates the `users` row (role hard-coded to PATIENT, never taken from
    the caller) and a matching `patients` row, in the caller's transaction.

    Self-registered patients get a `SELFREG-` prefixed patient_number
    (randomly suffixed, retried on the astronomically unlikely collision)
    rather than the sequential numbers staff assign - this keeps the two
    issuance paths from needing to coordinate a shared counter.
    """
    user_id = session.execute(
        text(
            """
            INSERT INTO users (auth_user_id, hospital_id, role_id, first_name, last_name, email)
            SELECT :auth_user_id, :hospital_id, r.id, :first_name, :last_name, :email
            FROM roles r WHERE r.name = :role_name
            RETURNING id
            """
        ),
        {
            "auth_user_id": str(auth_user_id),
            "hospital_id": str(hospital_id),
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "role_name": Role.PATIENT.value,
        },
    ).scalar_one()

    for attempt in range(5):
        patient_number = f"SELFREG-{uuid.uuid4().hex[:10].upper()}"
        try:
            with session.begin_nested():
                session.execute(
                    text(
                        """
                        INSERT INTO patients (user_id, hospital_id, patient_number)
                        VALUES (:user_id, :hospital_id, :patient_number)
                        """
                    ),
                    {"user_id": user_id, "hospital_id": str(hospital_id), "patient_number": patient_number},
                )
            break
        except IntegrityError:
            if attempt == 4:
                raise

    record = get_user_by_auth_id(session, auth_user_id)
    assert record is not None
    return record


def create_employee_user(
    session: Session,
    *,
    auth_user_id: uuid.UUID,
    email: str,
    first_name: str,
    last_name: str,
    hospital_id: uuid.UUID,
    department_id: uuid.UUID | None,
    role_id: uuid.UUID,
) -> uuid.UUID:
    """Creates the `users` row for an accepted employee invitation. Unlike
    create_patient_registration, role_id/hospital_id/department_id are
    passed in directly rather than resolved by name - the caller
    (app/services/invitation_service.py) already validated them against the
    invitation record, which is the actual source of truth here, not this
    function's caller-supplied arguments in isolation.
    """
    return session.execute(
        text(
            """
            INSERT INTO users (auth_user_id, hospital_id, role_id, department_id, first_name, last_name, email)
            VALUES (:auth_user_id, :hospital_id, :role_id, :department_id, :first_name, :last_name, :email)
            RETURNING id
            """
        ),
        {
            "auth_user_id": str(auth_user_id),
            "hospital_id": hospital_id,
            "role_id": role_id,
            "department_id": department_id,
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
        },
    ).scalar_one()


def create_doctor_profile(
    session: Session, *, user_id: uuid.UUID, hospital_id: uuid.UUID, department_id: uuid.UUID | None
) -> uuid.UUID:
    employee_number = f"EMP-{uuid.uuid4().hex[:10].upper()}"
    return session.execute(
        text(
            """
            INSERT INTO doctors (user_id, hospital_id, department_id, employee_number)
            VALUES (:user_id, :hospital_id, :department_id, :employee_number)
            RETURNING id
            """
        ),
        {
            "user_id": user_id,
            "hospital_id": hospital_id,
            "department_id": department_id,
            "employee_number": employee_number,
        },
    ).scalar_one()


def create_staff_profile(
    session: Session,
    *,
    user_id: uuid.UUID,
    hospital_id: uuid.UUID,
    department_id: uuid.UUID | None,
    designation: str,
) -> uuid.UUID:
    employee_number = f"EMP-{uuid.uuid4().hex[:10].upper()}"
    return session.execute(
        text(
            """
            INSERT INTO staff (user_id, hospital_id, department_id, employee_number, designation)
            VALUES (:user_id, :hospital_id, :department_id, :employee_number, :designation)
            RETURNING id
            """
        ),
        {
            "user_id": user_id,
            "hospital_id": hospital_id,
            "department_id": department_id,
            "employee_number": employee_number,
            "designation": designation,
        },
    ).scalar_one()
