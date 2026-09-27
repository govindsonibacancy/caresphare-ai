"""Clinical data access: the resource-ownership lookups Phase 4's
authorization layer uses, plus (Phase 6) the get_*/list_* functions the
structured hospital-data APIs read through.

Every list_* function bakes the caller's authorized scope into the SQL
WHERE clause itself (see the _*_scope_clause helpers) rather than fetching
broadly and filtering in Python - see docs/SECURE_DATA_APIS.md, "List-query
security". Filters passed by a caller are always ANDed onto that clause,
never substituted for it, so a filter can narrow an authorized result set
but never widen it.
"""

import datetime
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.permissions.roles import Role
from app.permissions.scope import UserScope


@dataclass(frozen=True)
class ResourceOwnership:
    hospital_id: uuid.UUID
    patient_id: uuid.UUID
    department_id: uuid.UUID | None = None


def get_patient_hospital_id(session: Session, patient_id: uuid.UUID) -> uuid.UUID | None:
    return session.execute(
        text("SELECT hospital_id FROM patients WHERE id = :id"), {"id": patient_id}
    ).scalar_one_or_none()


def get_doctor_hospital_id(session: Session, doctor_id: uuid.UUID) -> uuid.UUID | None:
    return session.execute(
        text("SELECT hospital_id FROM doctors WHERE id = :id"), {"id": doctor_id}
    ).scalar_one_or_none()


def get_staff_hospital_id(session: Session, staff_id: uuid.UUID) -> uuid.UUID | None:
    return session.execute(
        text("SELECT hospital_id FROM staff WHERE id = :id"), {"id": staff_id}
    ).scalar_one_or_none()


def get_department_hospital_id(session: Session, department_id: uuid.UUID) -> uuid.UUID | None:
    return session.execute(
        text("SELECT hospital_id FROM departments WHERE id = :id"), {"id": department_id}
    ).scalar_one_or_none()


def has_active_assignment(session: Session, *, doctor_id: uuid.UUID, patient_id: uuid.UUID) -> bool:
    return (
        session.execute(
            text(
                """
                SELECT 1 FROM doctor_patient_assignments
                WHERE doctor_id = :doctor_id AND patient_id = :patient_id AND is_active
                """
            ),
            {"doctor_id": doctor_id, "patient_id": patient_id},
        ).first()
        is not None
    )


def get_appointment_ownership(session: Session, appointment_id: uuid.UUID) -> ResourceOwnership | None:
    row = (
        session.execute(
            text("SELECT hospital_id, patient_id, department_id FROM appointments WHERE id = :id"),
            {"id": appointment_id},
        )
        .mappings()
        .first()
    )
    return ResourceOwnership(**row) if row else None


def get_medical_record_ownership(session: Session, record_id: uuid.UUID) -> ResourceOwnership | None:
    row = (
        session.execute(
            text("SELECT hospital_id, patient_id FROM medical_records WHERE id = :id"),
            {"id": record_id},
        )
        .mappings()
        .first()
    )
    return ResourceOwnership(**row) if row else None


def get_lab_report_ownership(session: Session, report_id: uuid.UUID) -> ResourceOwnership | None:
    row = (
        session.execute(
            text("SELECT hospital_id, patient_id FROM lab_reports WHERE id = :id"),
            {"id": report_id},
        )
        .mappings()
        .first()
    )
    return ResourceOwnership(**row) if row else None


def get_prescription_ownership(session: Session, prescription_id: uuid.UUID) -> ResourceOwnership | None:
    row = (
        session.execute(
            text("SELECT hospital_id, patient_id FROM prescriptions WHERE id = :id"),
            {"id": prescription_id},
        )
        .mappings()
        .first()
    )
    return ResourceOwnership(**row) if row else None


# --- Phase 6: scope-clause helpers -----------------------------------------
#
# These express exactly the same rules as can_access_patient/
# can_access_appointment (app/permissions/authorization.py) - as SQL WHERE
# fragments instead of a single-id boolean check, so list_* below can filter
# at the database rather than fetching broadly and checking each row in
# Python. Keeping the rule in one place per resource type (mirrored, not
# reimplemented independently) is deliberate: a single-item GET and a LIST
# must never disagree about what's authorized.


def _hospital_scope_clause(scope: UserScope) -> tuple[str, dict]:
    if scope.role is Role.SUPER_ADMIN:
        return "TRUE", {}
    return "hospital_id = :scope_hospital_id", {"scope_hospital_id": scope.hospital_id}


def _patient_relationship_clause(scope: UserScope, patient_column: str = "patient_id") -> tuple[str, dict]:
    """Mirrors can_access_patient's role branching. `patient_column` is
    "id" when scoping the `patients` table itself, or the default
    "patient_id" for any table with a patient_id foreign key. Always AND
    this with _hospital_scope_clause - this expresses only the
    patient-relationship half of can_access_patient.
    """
    if scope.role is Role.SUPER_ADMIN:
        return "TRUE", {}
    if scope.role is Role.PATIENT:
        return f"{patient_column} = :scope_patient_id", {"scope_patient_id": scope.patient_id}
    if scope.role is Role.DOCTOR:
        return (
            f"{patient_column} IN (SELECT patient_id FROM doctor_patient_assignments "
            "WHERE doctor_id = :scope_doctor_id AND is_active)",
            {"scope_doctor_id": scope.doctor_id},
        )
    # NURSE, RECEPTIONIST, STAFF, HOSPITAL_ADMIN: no patient-level clinical
    # relationship - see docs/AUTHORIZATION.md, "Limitations".
    return "FALSE", {}


def _patients_table_scope_clause(scope: UserScope) -> tuple[str, dict]:
    hospital_sql, hospital_params = _hospital_scope_clause(scope)
    patient_sql, patient_params = _patient_relationship_clause(scope, patient_column="id")
    if patient_sql == "FALSE":
        return "FALSE", {}
    return f"{hospital_sql} AND {patient_sql}", {**hospital_params, **patient_params}


def _patient_owned_table_scope_clause(scope: UserScope) -> tuple[str, dict]:
    """For medical_records/lab_reports/prescriptions: hospital_id +
    patient_id both live on the table itself."""
    hospital_sql, hospital_params = _hospital_scope_clause(scope)
    patient_sql, patient_params = _patient_relationship_clause(scope)
    if patient_sql == "FALSE":
        return "FALSE", {}
    return f"{hospital_sql} AND {patient_sql}", {**hospital_params, **patient_params}


def _appointment_scope_clause(scope: UserScope) -> tuple[str, dict]:
    """Mirrors can_access_appointment exactly: RECEPTIONIST/STAFF get
    department-wide access, HOSPITAL_ADMIN gets hospital-wide, PATIENT/
    DOCTOR follow the shared patient-relationship rule, NURSE gets none."""
    if scope.role is Role.SUPER_ADMIN:
        return "TRUE", {}
    if scope.role is Role.HOSPITAL_ADMIN:
        return "hospital_id = :scope_hospital_id", {"scope_hospital_id": scope.hospital_id}
    if scope.role in (Role.RECEPTIONIST, Role.STAFF):
        return (
            "hospital_id = :scope_hospital_id AND department_id = :scope_department_id",
            {"scope_hospital_id": scope.hospital_id, "scope_department_id": scope.department_id},
        )
    patient_sql, patient_params = _patient_relationship_clause(scope)
    if patient_sql == "FALSE":
        return "FALSE", {}
    return f"hospital_id = :scope_hospital_id AND {patient_sql}", {
        "scope_hospital_id": scope.hospital_id,
        **patient_params,
    }


def _paginate(
    session: Session,
    *,
    table: str,
    columns: str,
    where_sql: str,
    where_params: dict,
    order_by: str,
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
    if where_sql == "FALSE":
        return [], 0
    total = session.execute(text(f"SELECT count(*) FROM {table} WHERE {where_sql}"), where_params).scalar_one()
    rows = (
        session.execute(
            text(
                f"SELECT {columns} FROM {table} WHERE {where_sql} "
                f"ORDER BY {order_by} LIMIT :limit OFFSET :offset"
            ),
            {**where_params, "limit": limit, "offset": offset},
        )
        .mappings()
        .all()
    )
    return list(rows), total


# --- patients ---------------------------------------------------------

_PATIENT_COLUMNS = (
    "id, user_id, hospital_id, patient_number, date_of_birth, gender, blood_group, "
    "phone, address, emergency_contact_name, emergency_contact_phone, created_at, updated_at"
)


@dataclass(frozen=True)
class PatientRecord:
    id: uuid.UUID
    user_id: uuid.UUID | None
    hospital_id: uuid.UUID
    patient_number: str
    date_of_birth: datetime.date | None
    gender: str | None
    blood_group: str | None
    phone: str | None
    address: str | None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


def get_patient(session: Session, patient_id: uuid.UUID) -> PatientRecord | None:
    row = (
        session.execute(text(f"SELECT {_PATIENT_COLUMNS} FROM patients WHERE id = :id"), {"id": patient_id})
        .mappings()
        .first()
    )
    return PatientRecord(**row) if row else None


def list_patients(
    session: Session, scope: UserScope, *, limit: int, offset: int, sort_desc: bool = True
) -> tuple[list[PatientRecord], int]:
    where_sql, params = _patients_table_scope_clause(scope)
    rows, total = _paginate(
        session,
        table="patients",
        columns=_PATIENT_COLUMNS,
        where_sql=where_sql,
        where_params=params,
        order_by="created_at DESC" if sort_desc else "created_at ASC",
        limit=limit,
        offset=offset,
    )
    return [PatientRecord(**r) for r in rows], total


# --- doctors ------------------------------------------------------------

_DOCTOR_COLUMNS = (
    "id, user_id, hospital_id, department_id, employee_number, specialization, "
    "license_number, created_at, updated_at"
)


@dataclass(frozen=True)
class DoctorRecord:
    id: uuid.UUID
    user_id: uuid.UUID
    hospital_id: uuid.UUID
    department_id: uuid.UUID | None
    employee_number: str
    specialization: str | None
    license_number: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


def get_doctor(session: Session, doctor_id: uuid.UUID) -> DoctorRecord | None:
    row = (
        session.execute(text(f"SELECT {_DOCTOR_COLUMNS} FROM doctors WHERE id = :id"), {"id": doctor_id})
        .mappings()
        .first()
    )
    return DoctorRecord(**row) if row else None


def list_doctors(
    session: Session,
    scope: UserScope,
    *,
    limit: int,
    offset: int,
    department_id: uuid.UUID | None = None,
) -> tuple[list[DoctorRecord], int]:
    where_sql, params = _hospital_scope_clause(scope)
    if department_id is not None:
        where_sql = f"{where_sql} AND department_id = :filter_department_id"
        params = {**params, "filter_department_id": department_id}
    rows, total = _paginate(
        session,
        table="doctors",
        columns=_DOCTOR_COLUMNS,
        where_sql=where_sql,
        where_params=params,
        order_by="created_at DESC",
        limit=limit,
        offset=offset,
    )
    return [DoctorRecord(**r) for r in rows], total


# --- departments --------------------------------------------------------

_DEPARTMENT_COLUMNS = "id, hospital_id, name, code, description, is_active, created_at, updated_at"


@dataclass(frozen=True)
class DepartmentRecord:
    id: uuid.UUID
    hospital_id: uuid.UUID
    name: str
    code: str
    description: str | None
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


def get_department(session: Session, department_id: uuid.UUID) -> DepartmentRecord | None:
    row = (
        session.execute(
            text(f"SELECT {_DEPARTMENT_COLUMNS} FROM departments WHERE id = :id"), {"id": department_id}
        )
        .mappings()
        .first()
    )
    return DepartmentRecord(**row) if row else None


def list_hospital_departments(
    session: Session, scope: UserScope, *, limit: int, offset: int
) -> tuple[list[DepartmentRecord], int]:
    where_sql, params = _hospital_scope_clause(scope)
    rows, total = _paginate(
        session,
        table="departments",
        columns=_DEPARTMENT_COLUMNS,
        where_sql=where_sql,
        where_params=params,
        order_by="name ASC",
        limit=limit,
        offset=offset,
    )
    return [DepartmentRecord(**r) for r in rows], total


# --- appointments --------------------------------------------------------

_APPOINTMENT_COLUMNS = (
    "id, hospital_id, patient_id, doctor_id, department_id, appointment_date, "
    "appointment_time, status, reason, notes, created_at, updated_at"
)


@dataclass(frozen=True)
class AppointmentRecord:
    id: uuid.UUID
    hospital_id: uuid.UUID
    patient_id: uuid.UUID
    doctor_id: uuid.UUID
    department_id: uuid.UUID | None
    appointment_date: datetime.date
    appointment_time: datetime.time
    status: str
    reason: str | None
    notes: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


def get_appointment(session: Session, appointment_id: uuid.UUID) -> AppointmentRecord | None:
    row = (
        session.execute(
            text(f"SELECT {_APPOINTMENT_COLUMNS} FROM appointments WHERE id = :id"), {"id": appointment_id}
        )
        .mappings()
        .first()
    )
    return AppointmentRecord(**row) if row else None


_APPOINTMENT_SORT_COLUMNS = {"appointment_date": "appointment_date", "created_at": "created_at"}


def list_appointments(
    session: Session,
    scope: UserScope,
    *,
    limit: int,
    offset: int,
    patient_id: uuid.UUID | None = None,
    doctor_id: uuid.UUID | None = None,
    department_id: uuid.UUID | None = None,
    status: str | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    sort_by: str = "appointment_date",
    sort_desc: bool = True,
) -> tuple[list[AppointmentRecord], int]:
    where_sql, params = _appointment_scope_clause(scope)
    filters = []
    if patient_id is not None:
        filters.append("patient_id = :filter_patient_id")
        params["filter_patient_id"] = patient_id
    if doctor_id is not None:
        filters.append("doctor_id = :filter_doctor_id")
        params["filter_doctor_id"] = doctor_id
    if department_id is not None:
        filters.append("department_id = :filter_department_id")
        params["filter_department_id"] = department_id
    if status is not None:
        filters.append("status = :filter_status")
        params["filter_status"] = status
    if date_from is not None:
        filters.append("appointment_date >= :filter_date_from")
        params["filter_date_from"] = date_from
    if date_to is not None:
        filters.append("appointment_date <= :filter_date_to")
        params["filter_date_to"] = date_to
    if filters and where_sql != "FALSE":
        where_sql = " AND ".join([where_sql, *filters])

    column = _APPOINTMENT_SORT_COLUMNS[sort_by]  # caller (API layer) validates sort_by against an allowlist
    order_by = f"{column} DESC" if sort_desc else f"{column} ASC"

    rows, total = _paginate(
        session,
        table="appointments",
        columns=_APPOINTMENT_COLUMNS,
        where_sql=where_sql,
        where_params=params,
        order_by=order_by,
        limit=limit,
        offset=offset,
    )
    return [AppointmentRecord(**r) for r in rows], total


# --- medical records -----------------------------------------------------

_MEDICAL_RECORD_COLUMNS = (
    "id, hospital_id, patient_id, doctor_id, record_type, title, description, "
    "clinical_notes, recorded_at, created_at, updated_at"
)


@dataclass(frozen=True)
class MedicalRecordRecord:
    id: uuid.UUID
    hospital_id: uuid.UUID
    patient_id: uuid.UUID
    doctor_id: uuid.UUID
    record_type: str
    title: str
    description: str | None
    clinical_notes: str | None
    recorded_at: datetime.datetime
    created_at: datetime.datetime
    updated_at: datetime.datetime


def get_medical_record(session: Session, record_id: uuid.UUID) -> MedicalRecordRecord | None:
    row = (
        session.execute(
            text(f"SELECT {_MEDICAL_RECORD_COLUMNS} FROM medical_records WHERE id = :id"), {"id": record_id}
        )
        .mappings()
        .first()
    )
    return MedicalRecordRecord(**row) if row else None


def list_medical_records(
    session: Session,
    scope: UserScope,
    *,
    limit: int,
    offset: int,
    patient_id: uuid.UUID | None = None,
    doctor_id: uuid.UUID | None = None,
    record_type: str | None = None,
) -> tuple[list[MedicalRecordRecord], int]:
    where_sql, params = _patient_owned_table_scope_clause(scope)
    filters = []
    if patient_id is not None:
        filters.append("patient_id = :filter_patient_id")
        params["filter_patient_id"] = patient_id
    if doctor_id is not None:
        filters.append("doctor_id = :filter_doctor_id")
        params["filter_doctor_id"] = doctor_id
    if record_type is not None:
        filters.append("record_type = :filter_record_type")
        params["filter_record_type"] = record_type
    if filters and where_sql != "FALSE":
        where_sql = " AND ".join([where_sql, *filters])

    rows, total = _paginate(
        session,
        table="medical_records",
        columns=_MEDICAL_RECORD_COLUMNS,
        where_sql=where_sql,
        where_params=params,
        order_by="recorded_at DESC",
        limit=limit,
        offset=offset,
    )
    return [MedicalRecordRecord(**r) for r in rows], total


# --- lab reports ----------------------------------------------------------

_LAB_REPORT_COLUMNS = (
    "id, hospital_id, patient_id, ordered_by_doctor_id, test_name, test_code, result, "
    "unit, reference_range, status, reported_at, created_at, updated_at"
)


@dataclass(frozen=True)
class LabReportRecord:
    id: uuid.UUID
    hospital_id: uuid.UUID
    patient_id: uuid.UUID
    ordered_by_doctor_id: uuid.UUID
    test_name: str
    test_code: str | None
    result: str | None
    unit: str | None
    reference_range: str | None
    status: str
    reported_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


def get_lab_report(session: Session, report_id: uuid.UUID) -> LabReportRecord | None:
    row = (
        session.execute(text(f"SELECT {_LAB_REPORT_COLUMNS} FROM lab_reports WHERE id = :id"), {"id": report_id})
        .mappings()
        .first()
    )
    return LabReportRecord(**row) if row else None


def list_lab_reports(
    session: Session,
    scope: UserScope,
    *,
    limit: int,
    offset: int,
    patient_id: uuid.UUID | None = None,
    status: str | None = None,
) -> tuple[list[LabReportRecord], int]:
    where_sql, params = _patient_owned_table_scope_clause(scope)
    filters = []
    if patient_id is not None:
        filters.append("patient_id = :filter_patient_id")
        params["filter_patient_id"] = patient_id
    if status is not None:
        filters.append("status = :filter_status")
        params["filter_status"] = status
    if filters and where_sql != "FALSE":
        where_sql = " AND ".join([where_sql, *filters])

    rows, total = _paginate(
        session,
        table="lab_reports",
        columns=_LAB_REPORT_COLUMNS,
        where_sql=where_sql,
        where_params=params,
        order_by="created_at DESC",
        limit=limit,
        offset=offset,
    )
    return [LabReportRecord(**r) for r in rows], total


# --- prescriptions --------------------------------------------------------

_PRESCRIPTION_COLUMNS = (
    "id, hospital_id, patient_id, doctor_id, medication_name, dosage, frequency, "
    "route, duration, instructions, prescribed_at, created_at, updated_at"
)


@dataclass(frozen=True)
class PrescriptionRecord:
    id: uuid.UUID
    hospital_id: uuid.UUID
    patient_id: uuid.UUID
    doctor_id: uuid.UUID
    medication_name: str
    dosage: str | None
    frequency: str | None
    route: str | None
    duration: str | None
    instructions: str | None
    prescribed_at: datetime.datetime
    created_at: datetime.datetime
    updated_at: datetime.datetime


def get_prescription(session: Session, prescription_id: uuid.UUID) -> PrescriptionRecord | None:
    row = (
        session.execute(
            text(f"SELECT {_PRESCRIPTION_COLUMNS} FROM prescriptions WHERE id = :id"), {"id": prescription_id}
        )
        .mappings()
        .first()
    )
    return PrescriptionRecord(**row) if row else None


def list_prescriptions(
    session: Session,
    scope: UserScope,
    *,
    limit: int,
    offset: int,
    patient_id: uuid.UUID | None = None,
    doctor_id: uuid.UUID | None = None,
) -> tuple[list[PrescriptionRecord], int]:
    where_sql, params = _patient_owned_table_scope_clause(scope)
    filters = []
    if patient_id is not None:
        filters.append("patient_id = :filter_patient_id")
        params["filter_patient_id"] = patient_id
    if doctor_id is not None:
        filters.append("doctor_id = :filter_doctor_id")
        params["filter_doctor_id"] = doctor_id
    if filters and where_sql != "FALSE":
        where_sql = " AND ".join([where_sql, *filters])

    rows, total = _paginate(
        session,
        table="prescriptions",
        columns=_PRESCRIPTION_COLUMNS,
        where_sql=where_sql,
        where_params=params,
        order_by="prescribed_at DESC",
        limit=limit,
        offset=offset,
    )
    return [PrescriptionRecord(**r) for r in rows], total


# --- Phase 11: query-router support ----------------------------------------
#
# Two small additions for natural-language structured queries
# (app/services/structured_query_service.py) - neither introduces a new
# authorization rule; both reuse the exact scope clauses above.


@dataclass(frozen=True)
class PatientNameMatch:
    patient_id: uuid.UUID
    first_name: str
    last_name: str


def find_patients_by_name(
    session: Session, scope: UserScope, name_query: str, *, limit: int = 5
) -> list[PatientNameMatch]:
    """Resolves a natural-language patient-name reference to candidate
    patients. Scoped by the identical `_patients_table_scope_clause` the
    `patients` list endpoint uses (applied as a subquery on `patients.id`,
    so joining in `users` for the name match can never widen it) - name
    resolution itself can never reveal the existence of a patient outside
    the caller's own authorization boundary. See docs/QUERY_ROUTING.md,
    "Patient / resource references".
    """
    where_sql, params = _patients_table_scope_clause(scope)
    if where_sql == "FALSE":
        return []
    rows = (
        session.execute(
            text(
                f"""
                SELECT p.id AS patient_id, u.first_name, u.last_name
                FROM patients p
                JOIN users u ON u.id = p.user_id
                WHERE p.id IN (SELECT id FROM patients WHERE {where_sql})
                  AND (u.first_name || ' ' || u.last_name) ILIKE :name_pattern
                ORDER BY u.last_name, u.first_name
                LIMIT :limit
                """
            ),
            {**params, "name_pattern": f"%{name_query}%", "limit": limit},
        )
        .mappings()
        .all()
    )
    return [PatientNameMatch(**row) for row in rows]


def get_user_display_name(session: Session, user_id: uuid.UUID) -> str | None:
    """A small, bounded lookup - used only for a handful (<= a query's own
    `limit`) of already-authorized rows that need a human-readable name
    for an LLM-facing structured context (e.g. a doctor's name on an
    appointment) rather than a raw id. Never used in a loop over an
    unbounded result set."""
    row = session.execute(text("SELECT first_name, last_name FROM users WHERE id = :id"), {"id": user_id}).mappings().first()
    return f"{row['first_name']} {row['last_name']}" if row else None
