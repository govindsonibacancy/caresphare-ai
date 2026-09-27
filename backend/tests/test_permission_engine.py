"""Unit tests for the permission engine itself: has_permission() and
resolve_user_scope() (app/permissions/scope.py, authorization.py). These
call the functions directly against the live seeded database rather than
through HTTP - see test_authz_endpoint.py for the FastAPI-dependency-level
tests, and test_resource_authorization.py for can_access_*().
"""

import uuid

import pytest
from sqlalchemy import text

from app.permissions.authorization import has_permission
from app.permissions.roles import Role
from app.permissions.scope import resolve_user_scope
from app.schemas.auth import AuthenticatedUser

# Mirrors database/seeds/0001_demo_data.sql's role_permissions mapping.
# Asserting against a hand-written expected set (rather than re-deriving it
# from the same query the implementation uses) is what actually proves
# resolve_user_scope reads the seed correctly, rather than just proving it's
# internally consistent with itself.
EXPECTED_PERMISSIONS = {
    "PATIENT": {
        "view_own_profile",
        "view_own_appointments",
        "view_own_reports",
        "view_own_prescriptions",
    },
    "DOCTOR": {
        "view_own_profile",
        "view_assigned_patients",
        "view_patient_basic_information",
        "view_patient_medical_records",
        "view_vital_records",
        "create_clinical_notes",
        "create_appointments",
        "view_admission_information",
    },
    "NURSE": {
        "view_own_profile",
        "view_assigned_patients",
        "view_patient_basic_information",
        "view_vital_records",
        "update_nursing_notes",
        "view_admission_information",
    },
    "RECEPTIONIST": {
        "view_own_profile",
        "view_patient_basic_information",
        "manage_appointments",
        "create_appointments",
        "view_admission_information",
        "view_hospital_operations",
    },
    "STAFF": {
        "view_own_profile",
        "manage_appointments",
        "create_appointments",
        "view_admission_information",
        "manage_hospital_documents",
        "view_hospital_operations",
    },
    "HOSPITAL_ADMIN": {
        "view_own_profile",
        "manage_users",
        "manage_departments",
        "manage_hospital_documents",
        "manage_appointments",
        "view_admission_information",
        "view_hospital_operations",
        "view_hospital_analytics",
    },
    "SUPER_ADMIN": {
        "view_own_profile",
        "manage_users",
        "manage_roles",
        "manage_departments",
        "manage_hospital_documents",
        "view_hospital_operations",
        "view_hospital_analytics",
    },
}

SEED_EMAILS = {
    "PATIENT": "asha.verma@example-patient.example",
    "DOCTOR": "rohan.mehta@caresphere-demo.example",
    "NURSE": "kavya.iyer@caresphere-demo.example",
    "RECEPTIONIST": "sanjay.rao@caresphere-demo.example",
    "STAFF": "neha.joshi@caresphere-demo.example",
    "HOSPITAL_ADMIN": "vikram.singh@caresphere-demo.example",
    "SUPER_ADMIN": "meera.kapoor@caresphere-demo.example",
}


def _load_user(db_engine, email: str) -> AuthenticatedUser:
    with db_engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT u.id, u.auth_user_id, u.email, u.first_name, u.last_name,
                       u.hospital_id, r.name AS role, u.department_id, u.is_active
                FROM users u JOIN roles r ON r.id = u.role_id
                WHERE u.email = :email
                """
            ),
            {"email": email},
        ).mappings().one()
    return AuthenticatedUser(**row)


@pytest.mark.parametrize("role_name", sorted(EXPECTED_PERMISSIONS))
def test_role_permissions_resolved_from_database(db_engine, role_name):
    with db_engine.connect() as conn:
        user = _load_user(db_engine, SEED_EMAILS[role_name])
        scope = resolve_user_scope(conn, user)

    assert scope.role is Role(role_name)
    assert set(scope.permissions) == EXPECTED_PERMISSIONS[role_name]


def test_has_permission_true_and_false(db_engine):
    with db_engine.connect() as conn:
        user = _load_user(db_engine, SEED_EMAILS["DOCTOR"])
        scope = resolve_user_scope(conn, user)

    assert has_permission(scope, "view_patient_medical_records") is True
    assert has_permission(scope, "manage_users") is False


def test_scope_resolves_role_specific_ids(db_engine):
    with db_engine.connect() as conn:
        patient_user = _load_user(db_engine, SEED_EMAILS["PATIENT"])
        patient_scope = resolve_user_scope(conn, patient_user)
        doctor_user = _load_user(db_engine, SEED_EMAILS["DOCTOR"])
        doctor_scope = resolve_user_scope(conn, doctor_user)
        nurse_user = _load_user(db_engine, SEED_EMAILS["NURSE"])
        nurse_scope = resolve_user_scope(conn, nurse_user)

    assert patient_scope.patient_id is not None
    assert patient_scope.doctor_id is None
    assert patient_scope.staff_id is None

    assert doctor_scope.doctor_id is not None
    assert doctor_scope.patient_id is None

    assert nurse_scope.staff_id is not None
    assert nurse_scope.patient_id is None
    assert nurse_scope.doctor_id is None


def test_permission_mutation_changes_has_permission_result(db_engine):
    """Proves the engine reads role_permissions live, not a cached/hard-coded
    map: remove PATIENT's view_own_profile mapping, observe has_permission
    flip to False, restore it, observe it flip back - all against the same
    running process with no restart in between.
    """
    with db_engine.connect() as conn:
        user = _load_user(db_engine, SEED_EMAILS["PATIENT"])
        role_id, permission_id = conn.execute(
            text(
                """
                SELECT rp.role_id, rp.permission_id
                FROM role_permissions rp
                JOIN roles r ON r.id = rp.role_id
                JOIN permissions p ON p.id = rp.permission_id
                WHERE r.name = 'PATIENT' AND p.code = 'view_own_profile'
                """
            )
        ).one()

    with db_engine.connect() as conn:
        scope_before = resolve_user_scope(conn, user)
    assert has_permission(scope_before, "view_own_profile") is True

    try:
        with db_engine.connect() as conn:
            conn.execute(
                text("DELETE FROM role_permissions WHERE role_id = :r AND permission_id = :p"),
                {"r": role_id, "p": permission_id},
            )
            conn.commit()

        with db_engine.connect() as conn:
            scope_during = resolve_user_scope(conn, user)
        assert has_permission(scope_during, "view_own_profile") is False
    finally:
        with db_engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO role_permissions (role_id, permission_id) VALUES (:r, :p) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"r": role_id, "p": permission_id},
            )
            conn.commit()

    with db_engine.connect() as conn:
        scope_after = resolve_user_scope(conn, user)
    assert has_permission(scope_after, "view_own_profile") is True


def test_unknown_permission_code_is_simply_false(db_engine):
    with db_engine.connect() as conn:
        user = _load_user(db_engine, SEED_EMAILS["SUPER_ADMIN"])
        scope = resolve_user_scope(conn, user)

    assert has_permission(scope, "not_a_real_permission_" + uuid.uuid4().hex) is False
