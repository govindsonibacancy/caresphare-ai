import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.auth.jwt_verifier import TokenClaims, get_token_verifier
from app.core.db import get_engine
from app.main import app


@pytest.fixture(scope="session")
def db_engine():
    """A live connection to DATABASE_URL, seeded per database/README.md.

    Skips the tests that use this fixture (rather than failing) when no
    database is reachable, so `pytest` stays usable without Postgres running.
    """
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        pytest.skip(f"database not reachable at DATABASE_URL: {exc}")
    return engine


@dataclass
class _FakeVerifier:
    claims: TokenClaims

    def verify(self, token: str) -> TokenClaims:
        return self.claims


@pytest.fixture
def auth_as(db_engine):
    """`auth_as("some@email")` fakes get_token_verifier so subsequent
    TestClient requests (with any `Authorization: Bearer ...` header) are
    treated as that seeded user - the same technique test_auth_me.py and
    test_authz_endpoint.py use inline; centralized here for Phase 5's
    invitation tests. Returns the user's auth_user_id. Auto-clears the
    override after the test.
    """

    def _apply(email: str):
        with db_engine.connect() as conn:
            auth_user_id = conn.execute(
                text("SELECT auth_user_id FROM users WHERE email = :email"), {"email": email}
            ).scalar_one()
        app.dependency_overrides[get_token_verifier] = lambda: _FakeVerifier(
            TokenClaims(sub=str(auth_user_id), email=email, raw={})
        )
        return auth_user_id

    yield _apply
    app.dependency_overrides.pop(get_token_verifier, None)


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer whatever"}


# Phase 15: AUTHORIZATION_DENIED/CONVERSATION_DENIED audit rows are now a
# real side effect of any test that triggers a 403/404-ownership-denial
# through the real API - which happens across many pre-existing test
# files (permission checks, cross-hospital/cross-user isolation, etc.),
# not just the Phase 15 ones. Rather than editing every one of those
# files' own cleanup fixtures individually, this single autouse fixture
# cleans both up after any test that already touches the database -
# `request.fixturenames`/`getfixturevalue` (a standard pytest technique)
# means `db_engine` is only ever instantiated for a test that already
# uses it (directly or via `auth_as`/`second_hospital`/etc.), so a purely
# unit-level test file with no DB involvement at all still never pays for
# a database connection just because this fixture is autouse.
@pytest.fixture(autouse=True)
def _cleanup_denial_audit_logs(request):
    yield
    if "db_engine" not in request.fixturenames:
        return
    engine = request.getfixturevalue("db_engine")
    with engine.connect() as conn:
        conn.execute(
            text("DELETE FROM audit_logs WHERE action = ANY(:actions)"),
            {"actions": ["AUTHORIZATION_DENIED", "CONVERSATION_DENIED"]},
        )
        conn.commit()


_CLINICAL_ACCESS_AUDIT_ACTIONS = (
    "PATIENT_RECORD_ACCESSED",
    "MEDICAL_RECORD_ACCESSED",
    "LAB_REPORT_ACCESSED",
    "PRESCRIPTION_ACCESSED",
)


def _delete_clinical_access_audit_logs(db_engine) -> None:
    with db_engine.connect() as conn:
        conn.execute(
            text("DELETE FROM audit_logs WHERE action = ANY(:actions)"),
            {"actions": list(_CLINICAL_ACCESS_AUDIT_ACTIONS)},
        )
        conn.commit()


@pytest.fixture
def clean_clinical_audit_logs(db_engine):
    """Phase 6's detail-endpoint GETs (patients/medical-records/lab-reports/
    prescriptions) write an audit row on every successful access. Cleans
    both before and after: other tests in the same file that hit the same
    resource (without asking for this fixture) can otherwise leave a row
    behind that makes a later `.one()`-style assertion here see more than
    one row.
    """
    _delete_clinical_access_audit_logs(db_engine)
    yield
    _delete_clinical_access_audit_logs(db_engine)


@pytest.fixture
def second_hospital(db_engine):
    """A throwaway second hospital + department + HOSPITAL_ADMIN user, for
    tests that need to prove isolation between two hospitals rather than
    just within the single seeded one. Cleaned up unconditionally."""
    with db_engine.connect() as conn:
        hospital_id = conn.execute(
            text("INSERT INTO hospitals (name, code) VALUES ('Test Hospital B', 'TESTHOSP-B') RETURNING id")
        ).scalar_one()
        department_id = conn.execute(
            text(
                "INSERT INTO departments (hospital_id, name, code) "
                "VALUES (:hospital_id, 'Test Dept B', 'TESTDEPT-B') RETURNING id"
            ),
            {"hospital_id": hospital_id},
        ).scalar_one()
        role_id = conn.execute(text("SELECT id FROM roles WHERE name = 'HOSPITAL_ADMIN'")).scalar_one()
        auth_user_id = uuid.uuid4()
        admin_email = f"hospital-b-admin-{auth_user_id.hex[:8]}@example.com"
        user_id = conn.execute(
            text(
                "INSERT INTO users (auth_user_id, hospital_id, role_id, first_name, last_name, email) "
                "VALUES (:auth_user_id, :hospital_id, :role_id, 'Hospital B', 'Admin', :email) RETURNING id"
            ),
            {
                "auth_user_id": auth_user_id,
                "hospital_id": hospital_id,
                "role_id": role_id,
                "email": admin_email,
            },
        ).scalar_one()
        conn.commit()

    try:
        yield {
            "hospital_id": hospital_id,
            "department_id": department_id,
            "admin_user_id": user_id,
            "admin_email": admin_email,
        }
    finally:
        with db_engine.connect() as conn:
            conn.execute(text("DELETE FROM employee_invitations WHERE hospital_id = :h"), {"h": hospital_id})
            conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
            conn.execute(text("DELETE FROM departments WHERE id = :id"), {"id": department_id})
            conn.execute(text("DELETE FROM hospitals WHERE id = :id"), {"id": hospital_id})
            conn.commit()


@pytest.fixture
def second_hospital_clinical_data(db_engine, second_hospital):
    """Builds on `second_hospital`: a doctor (with an active assignment) and
    a patient in Hospital B, plus one appointment/medical_record/lab_report/
    prescription for them - a full clinical record set in a hospital other
    than the seeded CGH, for Phase 6's hospital-isolation tests. Torn down
    before `second_hospital` itself (pytest finalizes fixtures in reverse
    dependency order), so no FK ordering issue with that fixture's cleanup.
    """
    hospital_id = second_hospital["hospital_id"]
    department_id = second_hospital["department_id"]

    with db_engine.connect() as conn:
        doctor_role_id = conn.execute(text("SELECT id FROM roles WHERE name = 'DOCTOR'")).scalar_one()
        doctor_auth_id = uuid.uuid4()
        doctor_email = f"hospital-b-doctor-{doctor_auth_id.hex[:8]}@example.com"
        doctor_user_id = conn.execute(
            text(
                "INSERT INTO users (auth_user_id, hospital_id, role_id, department_id, first_name, last_name, email) "
                "VALUES (:auth_user_id, :hospital_id, :role_id, :department_id, 'Hospital B', 'Doctor', :email) "
                "RETURNING id"
            ),
            {
                "auth_user_id": doctor_auth_id,
                "hospital_id": hospital_id,
                "role_id": doctor_role_id,
                "department_id": department_id,
                "email": doctor_email,
            },
        ).scalar_one()
        doctor_id = conn.execute(
            text(
                "INSERT INTO doctors (user_id, hospital_id, department_id, employee_number) "
                "VALUES (:user_id, :hospital_id, :department_id, :employee_number) RETURNING id"
            ),
            {
                "user_id": doctor_user_id,
                "hospital_id": hospital_id,
                "department_id": department_id,
                "employee_number": f"TESTHOSP-B-D-{doctor_auth_id.hex[:6]}",
            },
        ).scalar_one()

        patient_id = conn.execute(
            text(
                "INSERT INTO patients (hospital_id, patient_number) VALUES (:hospital_id, :patient_number) "
                "RETURNING id"
            ),
            {"hospital_id": hospital_id, "patient_number": f"TESTHOSP-B-P-{doctor_auth_id.hex[:6]}"},
        ).scalar_one()

        conn.execute(
            text(
                "INSERT INTO doctor_patient_assignments (doctor_id, patient_id) VALUES (:doctor_id, :patient_id)"
            ),
            {"doctor_id": doctor_id, "patient_id": patient_id},
        )

        appointment_id = conn.execute(
            text(
                "INSERT INTO appointments (hospital_id, patient_id, doctor_id, department_id, appointment_date, appointment_time) "
                "VALUES (:hospital_id, :patient_id, :doctor_id, :department_id, CURRENT_DATE, '09:00') RETURNING id"
            ),
            {
                "hospital_id": hospital_id,
                "patient_id": patient_id,
                "doctor_id": doctor_id,
                "department_id": department_id,
            },
        ).scalar_one()

        medical_record_id = conn.execute(
            text(
                "INSERT INTO medical_records (hospital_id, patient_id, doctor_id, record_type, title) "
                "VALUES (:hospital_id, :patient_id, :doctor_id, 'CONSULTATION', 'Hospital B test record') "
                "RETURNING id"
            ),
            {"hospital_id": hospital_id, "patient_id": patient_id, "doctor_id": doctor_id},
        ).scalar_one()

        lab_report_id = conn.execute(
            text(
                "INSERT INTO lab_reports (hospital_id, patient_id, ordered_by_doctor_id, test_name) "
                "VALUES (:hospital_id, :patient_id, :doctor_id, 'Test Panel') RETURNING id"
            ),
            {"hospital_id": hospital_id, "patient_id": patient_id, "doctor_id": doctor_id},
        ).scalar_one()

        prescription_id = conn.execute(
            text(
                "INSERT INTO prescriptions (hospital_id, patient_id, doctor_id, medication_name) "
                "VALUES (:hospital_id, :patient_id, :doctor_id, 'Test Medication') RETURNING id"
            ),
            {"hospital_id": hospital_id, "patient_id": patient_id, "doctor_id": doctor_id},
        ).scalar_one()

        staff_role_id = conn.execute(text("SELECT id FROM roles WHERE name = 'STAFF'")).scalar_one()
        staff_auth_id = uuid.uuid4()
        staff_user_id = conn.execute(
            text(
                "INSERT INTO users (auth_user_id, hospital_id, role_id, department_id, first_name, last_name, email) "
                "VALUES (:auth_user_id, :hospital_id, :role_id, :department_id, 'Hospital B', 'Staff', :email) "
                "RETURNING id"
            ),
            {
                "auth_user_id": staff_auth_id,
                "hospital_id": hospital_id,
                "role_id": staff_role_id,
                "department_id": department_id,
                "email": f"hospital-b-staff-{staff_auth_id.hex[:8]}@example.com",
            },
        ).scalar_one()
        staff_id = conn.execute(
            text(
                "INSERT INTO staff (user_id, hospital_id, department_id, employee_number, designation) "
                "VALUES (:user_id, :hospital_id, :department_id, :employee_number, 'STAFF') RETURNING id"
            ),
            {
                "user_id": staff_user_id,
                "hospital_id": hospital_id,
                "department_id": department_id,
                "employee_number": f"TESTHOSP-B-S-{staff_auth_id.hex[:6]}",
            },
        ).scalar_one()

        conn.commit()

    try:
        yield {
            "hospital_id": hospital_id,
            "department_id": department_id,
            "doctor_id": doctor_id,
            "doctor_user_id": doctor_user_id,
            "doctor_email": doctor_email,
            "staff_id": staff_id,
            "staff_user_id": staff_user_id,
            "patient_id": patient_id,
            "appointment_id": appointment_id,
            "medical_record_id": medical_record_id,
            "lab_report_id": lab_report_id,
            "prescription_id": prescription_id,
        }
    finally:
        with db_engine.connect() as conn:
            conn.execute(text("DELETE FROM prescriptions WHERE id = :id"), {"id": prescription_id})
            conn.execute(text("DELETE FROM lab_reports WHERE id = :id"), {"id": lab_report_id})
            conn.execute(text("DELETE FROM medical_records WHERE id = :id"), {"id": medical_record_id})
            conn.execute(text("DELETE FROM appointments WHERE id = :id"), {"id": appointment_id})
            conn.execute(
                text("DELETE FROM doctor_patient_assignments WHERE doctor_id = :d AND patient_id = :p"),
                {"d": doctor_id, "p": patient_id},
            )
            conn.execute(text("DELETE FROM patients WHERE id = :id"), {"id": patient_id})
            conn.execute(text("DELETE FROM doctors WHERE id = :id"), {"id": doctor_id})
            conn.execute(text("DELETE FROM staff WHERE id = :id"), {"id": staff_id})
            conn.execute(
                text("DELETE FROM audit_logs WHERE actor_user_id IN (:d, :s)"),
                {"d": doctor_user_id, "s": staff_user_id},
            )
            conn.execute(text("DELETE FROM users WHERE id IN (:d, :s)"), {"d": doctor_user_id, "s": staff_user_id})
            conn.commit()


@pytest.fixture
def cleanup_document(db_engine):
    """`cleanup_document(document_id)` removes a test-created document: the
    row, its chunks, its access-control join rows, any audit rows about it,
    and - since LocalFileStorage writes are not transactional and survive a
    DB rollback - its on-disk file too. Shared by every Phase 7 document
    test file rather than each re-implementing it.
    """

    def _cleanup(document_id) -> None:
        from app.services.documents.storage import get_file_storage

        with db_engine.connect() as conn:
            filename = conn.execute(
                text("SELECT filename FROM documents WHERE id = :id"), {"id": document_id}
            ).scalar_one_or_none()
            conn.execute(
                text("DELETE FROM audit_logs WHERE resource_type = 'documents' AND resource_id = :id"),
                {"id": document_id},
            )
            conn.execute(text("DELETE FROM document_chunks WHERE document_id = :id"), {"id": document_id})
            conn.execute(text("DELETE FROM document_allowed_roles WHERE document_id = :id"), {"id": document_id})
            conn.execute(
                text("DELETE FROM document_authorized_doctors WHERE document_id = :id"), {"id": document_id}
            )
            conn.execute(text("DELETE FROM document_authorized_staff WHERE document_id = :id"), {"id": document_id})
            conn.execute(text("DELETE FROM documents WHERE id = :id"), {"id": document_id})
            conn.commit()

        if filename and "." in filename:
            extension = filename.rsplit(".", 1)[-1]
            try:
                get_file_storage().delete(f"{document_id}.{extension}")
            except OSError:
                pass

    return _cleanup
