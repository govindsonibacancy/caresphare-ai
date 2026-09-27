"""Verifies the Phase 2 schema and seed data described in database/README.md.

Requires a database reachable at DATABASE_URL with the migrations in
database/migrations/ and the seed in database/seeds/0001_demo_data.sql
applied. Tests are skipped (see conftest.db_engine) if no database is
reachable.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

EXPECTED_TABLES = {
    "hospitals",
    "departments",
    "roles",
    "permissions",
    "role_permissions",
    "users",
    "patients",
    "doctors",
    "staff",
    "doctor_patient_assignments",
    "appointments",
    "medical_records",
    "lab_reports",
    "prescriptions",
    "documents",
    "document_allowed_roles",
    "document_authorized_doctors",
    "document_authorized_staff",
    "document_chunks",
    "audit_logs",
}

EXPECTED_ROLES = {
    "PATIENT",
    "DOCTOR",
    "NURSE",
    "RECEPTIONIST",
    "STAFF",
    "HOSPITAL_ADMIN",
    "SUPER_ADMIN",
}

EXPECTED_PERMISSIONS = {
    "view_own_profile",
    "view_own_appointments",
    "view_own_reports",
    "view_own_prescriptions",
    "view_assigned_patients",
    "view_patient_basic_information",
    "view_patient_medical_records",
    "view_vital_records",
    "create_clinical_notes",
    "update_nursing_notes",
    "manage_appointments",
    "create_appointments",
    "view_admission_information",
    "manage_users",
    "manage_roles",
    "manage_departments",
    "manage_hospital_documents",
    "view_hospital_operations",
    "view_hospital_analytics",
}


def _scalar(db_engine, sql, **params):
    with db_engine.connect() as conn:
        return conn.execute(text(sql), params).scalar_one()


def _rows(db_engine, sql, **params):
    with db_engine.connect() as conn:
        return conn.execute(text(sql), params).fetchall()


# --- schema structure -------------------------------------------------


def test_required_tables_exist(db_engine):
    existing = {
        row[0]
        for row in _rows(
            db_engine,
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'",
        )
    }
    missing = EXPECTED_TABLES - existing
    assert not missing, f"missing tables: {missing}"


def test_document_chunks_embedding_is_pgvector(db_engine):
    column_type = _scalar(
        db_engine,
        """
        SELECT format_type(atttypid, atttypmod)
        FROM pg_attribute
        WHERE attrelid = 'document_chunks'::regclass
          AND attname = 'embedding'
        """,
    )
    assert column_type == "vector(384)"


def test_audit_logs_has_no_password_or_token_columns(db_engine):
    columns = {
        row[0]
        for row in _rows(
            db_engine,
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_logs'",
        )
    }
    forbidden = {"password", "password_hash", "access_token", "refresh_token"}
    assert not (columns & forbidden)


def test_users_has_no_password_column(db_engine):
    columns = {
        row[0]
        for row in _rows(
            db_engine,
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'users'",
        )
    }
    assert not any("password" in c or "token" in c for c in columns)


# --- seed data ----------------------------------------------------------


def test_roles_seeded(db_engine):
    names = {row[0] for row in _rows(db_engine, "SELECT name FROM roles")}
    assert names == EXPECTED_ROLES


def test_permissions_seeded(db_engine):
    codes = {row[0] for row in _rows(db_engine, "SELECT code FROM permissions")}
    assert EXPECTED_PERMISSIONS <= codes


def test_role_permissions_seeded(db_engine):
    count = _scalar(db_engine, "SELECT count(*) FROM role_permissions")
    assert count >= len(EXPECTED_ROLES)

    patient_permissions = {
        row[0]
        for row in _rows(
            db_engine,
            """
            SELECT p.code FROM role_permissions rp
            JOIN roles r ON r.id = rp.role_id
            JOIN permissions p ON p.id = rp.permission_id
            WHERE r.name = 'PATIENT'
            """,
        )
    }
    assert "view_own_profile" in patient_permissions
    assert "manage_users" not in patient_permissions


def test_demo_hospital_seeded(db_engine):
    row = _rows(db_engine, "SELECT name, is_active FROM hospitals WHERE code = 'CGH'")
    assert len(row) == 1
    assert row[0].is_active is True


def test_departments_seeded(db_engine):
    count = _scalar(
        db_engine,
        """
        SELECT count(*) FROM departments d
        JOIN hospitals h ON h.id = d.hospital_id
        WHERE h.code = 'CGH'
        """,
    )
    assert count >= 8


def test_patients_seeded(db_engine):
    count = _scalar(db_engine, "SELECT count(*) FROM patients")
    assert count >= 3


def test_doctors_seeded(db_engine):
    count = _scalar(db_engine, "SELECT count(*) FROM doctors")
    assert count >= 2


def test_doctor_patient_assignments_seeded(db_engine):
    count = _scalar(
        db_engine, "SELECT count(*) FROM doctor_patient_assignments WHERE is_active"
    )
    assert count >= 3

    # Doctor A (CGH-D-0001) must be assigned to Patient A (CGH-P-0001) but
    # not to Patient C (CGH-P-0003) - the seed's deliberate allow/deny pair
    # for future authorization tests.
    assigned = {
        row[0]
        for row in _rows(
            db_engine,
            """
            SELECT p.patient_number
            FROM doctor_patient_assignments dpa
            JOIN doctors doc ON doc.id = dpa.doctor_id
            JOIN patients p ON p.id = dpa.patient_id
            WHERE doc.employee_number = 'CGH-D-0001' AND dpa.is_active
            """,
        )
    }
    assert "CGH-P-0001" in assigned
    assert "CGH-P-0003" not in assigned


def test_appointments_seeded(db_engine):
    assert _scalar(db_engine, "SELECT count(*) FROM appointments") >= 1


def test_medical_records_seeded(db_engine):
    assert _scalar(db_engine, "SELECT count(*) FROM medical_records") >= 1


def test_lab_reports_seeded(db_engine):
    assert _scalar(db_engine, "SELECT count(*) FROM lab_reports") >= 1


def test_prescriptions_seeded(db_engine):
    assert _scalar(db_engine, "SELECT count(*) FROM prescriptions") >= 1


def test_documents_and_chunks_schema_ready(db_engine):
    # No documents are ingested yet (that's a later phase) - only the
    # schema is verified here.
    assert _scalar(db_engine, "SELECT count(*) FROM documents") == 0
    assert _scalar(db_engine, "SELECT count(*) FROM document_chunks") == 0


def test_audit_log_schema_ready(db_engine):
    assert _scalar(db_engine, "SELECT count(*) FROM audit_logs") == 0


# --- constraints ----------------------------------------------------------


def test_duplicate_auth_user_id_rejected(db_engine):
    with db_engine.connect() as conn:
        trans = conn.begin()
        try:
            existing_auth_user_id = conn.execute(
                text("SELECT auth_user_id FROM users LIMIT 1")
            ).scalar_one()
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO users (auth_user_id, hospital_id, role_id, first_name, last_name, email)
                        SELECT :auth_user_id, hospital_id, role_id, 'Duplicate', 'User', 'duplicate-test@example.test'
                        FROM users WHERE auth_user_id = :auth_user_id
                        """
                    ),
                    {"auth_user_id": existing_auth_user_id},
                )
        finally:
            trans.rollback()


def test_duplicate_patient_number_within_hospital_rejected(db_engine):
    with db_engine.connect() as conn:
        trans = conn.begin()
        try:
            existing = conn.execute(
                text("SELECT hospital_id, patient_number FROM patients LIMIT 1")
            ).one()
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO patients (hospital_id, patient_number)
                        VALUES (:hospital_id, :patient_number)
                        """
                    ),
                    {
                        "hospital_id": existing.hospital_id,
                        "patient_number": existing.patient_number,
                    },
                )
        finally:
            trans.rollback()


def test_invalid_foreign_key_rejected(db_engine):
    with db_engine.connect() as conn:
        trans = conn.begin()
        try:
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO hospitals (name, code)
                        SELECT 'Nonexistent', 'NOPE'
                        WHERE NOT EXISTS (SELECT 1 FROM hospitals WHERE code = 'NOPE')
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO departments (hospital_id, name, code)
                        VALUES ('00000000-0000-0000-0000-000000000000', 'Ghost', 'GHOST')
                        """
                    )
                )
        finally:
            trans.rollback()


def test_doctor_patient_assignment_rejects_unknown_doctor(db_engine):
    with db_engine.connect() as conn:
        trans = conn.begin()
        try:
            patient_id = conn.execute(
                text("SELECT id FROM patients LIMIT 1")
            ).scalar_one()
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO doctor_patient_assignments (doctor_id, patient_id)
                        VALUES ('00000000-0000-0000-0000-000000000000', :patient_id)
                        """
                    ),
                    {"patient_id": patient_id},
                )
        finally:
            trans.rollback()
