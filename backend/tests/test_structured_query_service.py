"""app/services/structured_query_service.py: calls `run_structured_query`
directly against a real database with hand-built `UserScope` objects - no
HTTP, no LLM dependency at all (this module never calls Ollama). Proves
that every structured intent reuses the exact Phase 4-6 authorization
model: permission checks compose existing `Permission` values, and every
repository call is the exact scope-clause-protected `clinical_repository`
function Phase 6's own REST endpoints use. See docs/QUERY_ROUTING.md,
"Authorization boundary".
"""

import uuid

import pytest
from sqlalchemy import text

from app.permissions.roles import Role
from app.permissions.scope import UserScope
from app.repositories import clinical_repository
from app.routing.intents import StructuredIntent
from app.services.structured_query_service import StructuredOutcome, run_structured_query


@pytest.fixture
def session(db_engine):
    from app.core.db import get_session_factory

    s = get_session_factory()()
    yield s
    s.close()


HOSPITAL_ADMIN_EMAIL = "vikram.singh@caresphere-demo.example"
SUPER_ADMIN_EMAIL = "meera.kapoor@caresphere-demo.example"
STAFF_EMAIL = "neha.joshi@caresphere-demo.example"
DOCTOR_EMAIL = "rohan.mehta@caresphere-demo.example"  # assigned to Asha Verma
DOCTOR_2_EMAIL = "priya.nair@caresphere-demo.example"  # NOT assigned to Asha Verma
NURSE_EMAIL = "kavya.iyer@caresphere-demo.example"
RECEPTIONIST_EMAIL = "sanjay.rao@caresphere-demo.example"
PATIENT_EMAIL = "asha.verma@example-patient.example"


def _scope_for(db_engine, email: str) -> UserScope:
    with db_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT u.id, u.auth_user_id, u.hospital_id, u.department_id, r.name AS role "
                "FROM users u JOIN roles r ON r.id = u.role_id WHERE u.email = :email"
            ),
            {"email": email},
        ).mappings().one()
    role = Role(row["role"])
    permissions = clinical_repository_permission_lookup(db_engine, role.value)
    patient_id = doctor_id = staff_id = None
    with db_engine.connect() as conn:
        if role is Role.PATIENT:
            patient_id = conn.execute(text("SELECT id FROM patients WHERE user_id = :u"), {"u": row["id"]}).scalar_one_or_none()
        if role is Role.DOCTOR:
            doctor_id = conn.execute(text("SELECT id FROM doctors WHERE user_id = :u"), {"u": row["id"]}).scalar_one_or_none()
        if role in (Role.NURSE, Role.RECEPTIONIST, Role.STAFF):
            staff_id = conn.execute(text("SELECT id FROM staff WHERE user_id = :u"), {"u": row["id"]}).scalar_one_or_none()
    return UserScope(
        user_id=row["id"],
        auth_user_id=row["auth_user_id"],
        role=role,
        permissions=permissions,
        hospital_id=row["hospital_id"],
        department_id=row["department_id"],
        patient_id=patient_id,
        doctor_id=doctor_id,
        staff_id=staff_id,
    )


def clinical_repository_permission_lookup(db_engine, role_name: str) -> frozenset[str]:
    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT p.code FROM role_permissions rp JOIN roles r ON r.id = rp.role_id "
                "JOIN permissions p ON p.id = rp.permission_id WHERE r.name = :role"
            ),
            {"role": role_name},
        ).scalars()
        return frozenset(rows)


# --- MY_APPOINTMENTS: authorized vs missing-permission --------------------


def test_patient_sees_own_appointments(db_engine, session):
    scope = _scope_for(db_engine, PATIENT_EMAIL)
    result = run_structured_query(session, scope=scope, intent=StructuredIntent.MY_APPOINTMENTS, entity_reference=None)
    assert result.outcome == StructuredOutcome.OK
    assert result.source == "appointments"
    assert len(result.records) >= 1
    assert all("appointment_id" in r for r in result.records)


def test_doctor_sees_own_assigned_appointments(db_engine, session):
    scope = _scope_for(db_engine, DOCTOR_EMAIL)
    result = run_structured_query(session, scope=scope, intent=StructuredIntent.MY_APPOINTMENTS, entity_reference=None)
    assert result.outcome == StructuredOutcome.OK
    assert all(r["doctor"] for r in result.records)


def test_nurse_lacks_appointment_permission_gets_no_data(db_engine, session):
    """NURSE holds none of view_own_appointments/create_appointments/
    manage_appointments in the seed (matches docs/AUTHORIZATION.md) - the
    exact same rule app/api/appointments.py's LIST_PERMISSIONS already
    enforces, reused here rather than reimplemented."""
    scope = _scope_for(db_engine, NURSE_EMAIL)
    result = run_structured_query(session, scope=scope, intent=StructuredIntent.MY_APPOINTMENTS, entity_reference=None)
    assert result.outcome == StructuredOutcome.NO_DATA
    assert result.records == []


def test_super_admin_lacks_appointment_permission_gets_no_data(db_engine, session):
    """SUPER_ADMIN's seeded permissions do not include any appointment
    permission either - matches docs/SECURE_DATA_APIS.md's documented
    SUPER_ADMIN behavior (broad on doctors/departments, not on clinical
    resources) exactly; Phase 11 does not introduce a new bypass."""
    scope = _scope_for(db_engine, SUPER_ADMIN_EMAIL)
    result = run_structured_query(session, scope=scope, intent=StructuredIntent.MY_APPOINTMENTS, entity_reference=None)
    assert result.outcome == StructuredOutcome.NO_DATA


# --- MY_MEDICAL_RECORDS / MY_LAB_REPORTS / MY_PRESCRIPTIONS ----------------


def test_doctor_lab_reports_query_returns_assigned_patients_reports(db_engine, session):
    """`_patient_owned_table_scope_clause`'s DOCTOR branch (reused
    unmodified from clinical_repository.py) means a DOCTOR's "lab reports"
    query naturally resolves to their *assigned patients'* lab reports,
    not an empty set - the same scope rule Phase 6's own lab-reports
    endpoint already applies for a DOCTOR caller. This is not a new
    behavior Phase 11 introduces; it is what the existing scope clause has
    always meant for this role."""
    scope = _scope_for(db_engine, DOCTOR_EMAIL)
    result = run_structured_query(session, scope=scope, intent=StructuredIntent.MY_LAB_REPORTS, entity_reference=None)
    assert result.outcome == StructuredOutcome.OK
    assert all("report_id" in r for r in result.records)


def test_receptionist_lacks_permission_for_medical_records(db_engine, session):
    scope = _scope_for(db_engine, RECEPTIONIST_EMAIL)
    result = run_structured_query(session, scope=scope, intent=StructuredIntent.MY_MEDICAL_RECORDS, entity_reference=None)
    assert result.outcome == StructuredOutcome.NO_DATA


# --- PATIENT_APPOINTMENTS: entity resolution + authorization --------------


def test_assigned_doctor_can_find_named_patients_appointments(db_engine, session):
    scope = _scope_for(db_engine, DOCTOR_EMAIL)
    result = run_structured_query(
        session, scope=scope, intent=StructuredIntent.PATIENT_APPOINTMENTS, entity_reference="Asha Verma"
    )
    assert result.outcome == StructuredOutcome.OK
    assert len(result.records) >= 1


def test_unassigned_doctor_cannot_find_named_patient(db_engine, session):
    """priya.nair has no active doctor_patient_assignments row for Asha
    Verma - find_patients_by_name's scope clause (identical to
    list_patients') excludes her entirely from name resolution, not just
    from the appointment data."""
    scope = _scope_for(db_engine, DOCTOR_2_EMAIL)
    result = run_structured_query(
        session, scope=scope, intent=StructuredIntent.PATIENT_APPOINTMENTS, entity_reference="Asha Verma"
    )
    assert result.outcome == StructuredOutcome.NO_DATA
    assert result.records == []


def test_receptionist_has_no_patient_relationship_for_named_lookup(db_engine, session):
    """RECEPTIONIST holds view_patient_basic_information (the permission
    gate passes) but has no patient-level relationship at all in
    _patient_relationship_clause - defense in depth: the SQL scope clause
    excludes them even though the permission check alone would not have."""
    scope = _scope_for(db_engine, RECEPTIONIST_EMAIL)
    result = run_structured_query(
        session, scope=scope, intent=StructuredIntent.PATIENT_APPOINTMENTS, entity_reference="Asha Verma"
    )
    assert result.outcome == StructuredOutcome.NO_DATA


def test_no_entity_reference_gets_no_data_not_an_error(db_engine, session):
    scope = _scope_for(db_engine, DOCTOR_EMAIL)
    result = run_structured_query(
        session, scope=scope, intent=StructuredIntent.PATIENT_APPOINTMENTS, entity_reference=None
    )
    assert result.outcome == StructuredOutcome.NO_DATA


def test_nonexistent_patient_name_gets_no_data(db_engine, session):
    scope = _scope_for(db_engine, DOCTOR_EMAIL)
    result = run_structured_query(
        session,
        scope=scope,
        intent=StructuredIntent.PATIENT_APPOINTMENTS,
        entity_reference="Zzqx Nonexistentperson",
    )
    assert result.outcome == StructuredOutcome.NO_DATA


# --- ambiguous entity resolution -------------------------------------------


@pytest.fixture
def duplicate_named_patient(db_engine):
    """A second CGH patient named exactly "Asha Verma", assigned to the
    same doctor as the real seeded one - so a name lookup for "Asha Verma"
    genuinely matches two authorized patients."""
    with db_engine.connect() as conn:
        hospital_id, department_id = conn.execute(
            text("SELECT hospital_id, department_id FROM users WHERE email = :e"), {"e": DOCTOR_EMAIL}
        ).one()
        doctor_id = conn.execute(text("SELECT id FROM doctors WHERE user_id = (SELECT id FROM users WHERE email = :e)"), {"e": DOCTOR_EMAIL}).scalar_one()
        patient_role_id = conn.execute(text("SELECT id FROM roles WHERE name = 'PATIENT'")).scalar_one()
        auth_id = uuid.uuid4()
        user_id = conn.execute(
            text(
                "INSERT INTO users (auth_user_id, hospital_id, role_id, first_name, last_name, email) "
                "VALUES (:auth_id, :hospital_id, :role_id, 'Asha', 'Verma', :email) RETURNING id"
            ),
            {"auth_id": auth_id, "hospital_id": hospital_id, "role_id": patient_role_id, "email": f"asha-dup-{auth_id.hex[:8]}@example.com"},
        ).scalar_one()
        patient_id = conn.execute(
            text("INSERT INTO patients (user_id, hospital_id, patient_number) VALUES (:u, :h, :n) RETURNING id"),
            {"u": user_id, "h": hospital_id, "n": f"DUPTEST-{auth_id.hex[:6]}"},
        ).scalar_one()
        conn.execute(
            text("INSERT INTO doctor_patient_assignments (doctor_id, patient_id) VALUES (:d, :p)"),
            {"d": doctor_id, "p": patient_id},
        )
        conn.commit()

    yield patient_id

    with db_engine.connect() as conn:
        conn.execute(text("DELETE FROM doctor_patient_assignments WHERE patient_id = :p"), {"p": patient_id})
        conn.execute(text("DELETE FROM patients WHERE id = :p"), {"p": patient_id})
        conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
        conn.commit()


def test_multiple_matching_patients_is_ambiguous_not_a_guess(db_engine, session, duplicate_named_patient):
    scope = _scope_for(db_engine, DOCTOR_EMAIL)
    result = run_structured_query(
        session, scope=scope, intent=StructuredIntent.PATIENT_APPOINTMENTS, entity_reference="Asha Verma"
    )
    assert result.outcome == StructuredOutcome.AMBIGUOUS
    assert result.records == []
    assert len(result.ambiguous_candidates) == 2
    assert all("Asha Verma" in c for c in result.ambiguous_candidates)


# --- hospital isolation -----------------------------------------------------


def test_hospital_b_doctor_cannot_find_hospital_a_patient_by_name(db_engine, session, second_hospital_clinical_data):
    scope = UserScope(
        user_id=second_hospital_clinical_data["doctor_user_id"],
        auth_user_id=second_hospital_clinical_data["doctor_user_id"],
        role=Role.DOCTOR,
        permissions=clinical_repository_permission_lookup(db_engine, "DOCTOR"),
        hospital_id=second_hospital_clinical_data["hospital_id"],
        department_id=second_hospital_clinical_data["department_id"],
        patient_id=None,
        doctor_id=second_hospital_clinical_data["doctor_id"],
        staff_id=None,
    )
    result = run_structured_query(
        session, scope=scope, intent=StructuredIntent.PATIENT_APPOINTMENTS, entity_reference="Asha Verma"
    )
    assert result.outcome == StructuredOutcome.NO_DATA


def test_hospital_a_doctor_directory_never_includes_hospital_b_doctors(db_engine, session, second_hospital_clinical_data):
    scope = _scope_for(db_engine, HOSPITAL_ADMIN_EMAIL)
    result = run_structured_query(session, scope=scope, intent=StructuredIntent.DOCTOR_DIRECTORY, entity_reference=None)
    assert result.outcome == StructuredOutcome.OK
    doctor_ids = {r["doctor_id"] for r in result.records}
    assert str(second_hospital_clinical_data["doctor_id"]) not in doctor_ids


# --- SUPER_ADMIN behavior preserved -----------------------------------------


def test_super_admin_doctor_directory_is_cross_hospital(db_engine, session, second_hospital_clinical_data):
    """Matches docs/SECURE_DATA_APIS.md's documented SUPER_ADMIN behavior
    (view_hospital_operations grants doctors/departments visibility,
    cross-hospital) exactly - Phase 11 introduces no new bypass, it only
    reaches the same existing list_doctors() SUPER_ADMIN already had."""
    scope = _scope_for(db_engine, SUPER_ADMIN_EMAIL)
    result = run_structured_query(session, scope=scope, intent=StructuredIntent.DOCTOR_DIRECTORY, entity_reference=None)
    assert result.outcome == StructuredOutcome.OK
    doctor_ids = {r["doctor_id"] for r in result.records}
    assert str(second_hospital_clinical_data["doctor_id"]) in doctor_ids


# --- directory intents ------------------------------------------------------


def test_staff_can_see_department_directory(db_engine, session):
    scope = _scope_for(db_engine, STAFF_EMAIL)
    result = run_structured_query(session, scope=scope, intent=StructuredIntent.DEPARTMENT_DIRECTORY, entity_reference=None)
    assert result.outcome == StructuredOutcome.OK
    assert len(result.records) >= 1
    assert all({"department_id", "name", "code"} <= r.keys() for r in result.records)


def test_doctor_lacks_permission_for_department_directory(db_engine, session):
    """DOCTOR has no view_hospital_operations in the seed."""
    scope = _scope_for(db_engine, DOCTOR_EMAIL)
    result = run_structured_query(session, scope=scope, intent=StructuredIntent.DEPARTMENT_DIRECTORY, entity_reference=None)
    assert result.outcome == StructuredOutcome.NO_DATA


# --- data minimization -------------------------------------------------------


def test_structured_records_never_include_internal_authorization_fields(db_engine, session):
    scope = _scope_for(db_engine, PATIENT_EMAIL)
    result = run_structured_query(session, scope=scope, intent=StructuredIntent.MY_APPOINTMENTS, entity_reference=None)
    assert result.outcome == StructuredOutcome.OK
    for record in result.records:
        for forbidden in ("hospital_id", "patient_id", "user_id", "role_id", "permission"):
            assert forbidden not in record
