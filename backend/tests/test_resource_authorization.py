"""Resource-relationship authorization: can_access_patient/appointment/
medical_record/lab_report/prescription (app/permissions/authorization.py).

These are the ALLOW/DENY matrices Phase 4's instructions specify directly,
built against the seeded doctor-patient assignments:
    Doctor A (CGH-D-0001) -> Patient A (CGH-P-0001), Patient B (CGH-P-0002)
    Doctor B (CGH-D-0002) -> Patient C (CGH-P-0003)
"""

import uuid

import pytest
from sqlalchemy import text

from app.permissions.authorization import (
    can_access_appointment,
    can_access_lab_report,
    can_access_medical_record,
    can_access_patient,
    can_access_prescription,
)
from app.permissions.roles import Role
from app.permissions.scope import UserScope


def _scope(
    db_engine,
    *,
    role: Role,
    patient_number: str | None = None,
    employee_number: str | None = None,
    hospital_id=None,
    department_id=None,
) -> UserScope:
    """Builds a UserScope directly (rather than via resolve_user_scope) so
    these tests can target seeded patients/doctors by their human-readable
    numbers without needing a real Supabase-linked user for every one.
    """
    with db_engine.connect() as conn:
        if hospital_id is None:
            hospital_id = conn.execute(text("SELECT id FROM hospitals WHERE code = 'CGH'")).scalar_one()
        patient_id = None
        if patient_number is not None:
            patient_id = conn.execute(
                text("SELECT id FROM patients WHERE patient_number = :n"), {"n": patient_number}
            ).scalar_one()
        doctor_id = None
        if employee_number is not None and role is Role.DOCTOR:
            doctor_id = conn.execute(
                text("SELECT id FROM doctors WHERE employee_number = :n"), {"n": employee_number}
            ).scalar_one()

    return UserScope(
        user_id=uuid.uuid4(),
        auth_user_id=uuid.uuid4(),
        role=role,
        permissions=frozenset(),
        hospital_id=hospital_id,
        department_id=department_id,
        patient_id=patient_id,
        doctor_id=doctor_id,
        staff_id=None,
    )


def _patient_id(db_engine, patient_number: str) -> uuid.UUID:
    with db_engine.connect() as conn:
        return conn.execute(
            text("SELECT id FROM patients WHERE patient_number = :n"), {"n": patient_number}
        ).scalar_one()


def _one_id(db_engine, sql: str, **params) -> uuid.UUID | None:
    with db_engine.connect() as conn:
        return conn.execute(text(sql), params).scalar_one_or_none()


# --- Patient tests -----------------------------------------------------


def test_patient_can_access_own_patient_record(db_engine):
    scope = _scope(db_engine, role=Role.PATIENT, patient_number="CGH-P-0001")
    with db_engine.connect() as conn:
        assert can_access_patient(conn, scope, _patient_id(db_engine, "CGH-P-0001")) is True


def test_patient_cannot_access_another_patient_record(db_engine):
    scope = _scope(db_engine, role=Role.PATIENT, patient_number="CGH-P-0001")
    with db_engine.connect() as conn:
        assert can_access_patient(conn, scope, _patient_id(db_engine, "CGH-P-0002")) is False


def test_patient_a_cannot_access_patient_b_medical_record(db_engine):
    scope = _scope(db_engine, role=Role.PATIENT, patient_number="CGH-P-0001")
    record_id = _one_id(
        db_engine,
        "SELECT mr.id FROM medical_records mr JOIN patients p ON p.id = mr.patient_id "
        "WHERE p.patient_number = 'CGH-P-0002' LIMIT 1",
    )
    assert record_id is not None
    with db_engine.connect() as conn:
        assert can_access_medical_record(conn, scope, record_id) is False


def test_patient_a_cannot_access_patient_b_lab_report(db_engine):
    scope = _scope(db_engine, role=Role.PATIENT, patient_number="CGH-P-0001")
    report_id = _one_id(
        db_engine,
        "SELECT lr.id FROM lab_reports lr JOIN patients p ON p.id = lr.patient_id "
        "WHERE p.patient_number = 'CGH-P-0002' LIMIT 1",
    )
    assert report_id is not None
    with db_engine.connect() as conn:
        assert can_access_lab_report(conn, scope, report_id) is False


def test_patient_a_cannot_access_patient_b_prescription(db_engine):
    scope = _scope(db_engine, role=Role.PATIENT, patient_number="CGH-P-0001")
    prescription_id = _one_id(
        db_engine,
        "SELECT pr.id FROM prescriptions pr JOIN patients p ON p.id = pr.patient_id "
        "WHERE p.patient_number = 'CGH-P-0002' LIMIT 1",
    )
    assert prescription_id is not None
    with db_engine.connect() as conn:
        assert can_access_prescription(conn, scope, prescription_id) is False


def test_patient_a_cannot_access_patient_b_appointment(db_engine):
    scope = _scope(db_engine, role=Role.PATIENT, patient_number="CGH-P-0001")
    appointment_id = _one_id(
        db_engine,
        "SELECT a.id FROM appointments a JOIN patients p ON p.id = a.patient_id "
        "WHERE p.patient_number = 'CGH-P-0002' LIMIT 1",
    )
    assert appointment_id is not None
    with db_engine.connect() as conn:
        assert can_access_appointment(conn, scope, appointment_id) is False


def test_patient_can_access_own_appointment(db_engine):
    scope = _scope(db_engine, role=Role.PATIENT, patient_number="CGH-P-0001")
    appointment_id = _one_id(
        db_engine,
        "SELECT a.id FROM appointments a JOIN patients p ON p.id = a.patient_id "
        "WHERE p.patient_number = 'CGH-P-0001' LIMIT 1",
    )
    assert appointment_id is not None
    with db_engine.connect() as conn:
        assert can_access_appointment(conn, scope, appointment_id) is True


# --- Doctor tests (using the seed's doctor_patient_assignments) --------


def test_doctor_a_can_access_patient_a_and_b(db_engine):
    scope = _scope(db_engine, role=Role.DOCTOR, employee_number="CGH-D-0001")
    with db_engine.connect() as conn:
        assert can_access_patient(conn, scope, _patient_id(db_engine, "CGH-P-0001")) is True
        assert can_access_patient(conn, scope, _patient_id(db_engine, "CGH-P-0002")) is True


def test_doctor_a_cannot_access_patient_c(db_engine):
    scope = _scope(db_engine, role=Role.DOCTOR, employee_number="CGH-D-0001")
    with db_engine.connect() as conn:
        assert can_access_patient(conn, scope, _patient_id(db_engine, "CGH-P-0003")) is False


def test_doctor_b_can_access_patient_c_but_not_patient_a(db_engine):
    scope = _scope(db_engine, role=Role.DOCTOR, employee_number="CGH-D-0002")
    with db_engine.connect() as conn:
        assert can_access_patient(conn, scope, _patient_id(db_engine, "CGH-P-0003")) is True
        assert can_access_patient(conn, scope, _patient_id(db_engine, "CGH-P-0001")) is False


def test_doctor_a_can_access_patient_a_medical_record_authored_by_another_doctor(db_engine):
    """Assignment to the *patient* governs access, not authorship of the
    specific record - the seed's Patient B record was written by Doctor A
    (the only doctor assigned to Patient B), so this also implicitly checks
    the positive case; the explicit point is that can_access_medical_record
    never looks at medical_records.doctor_id."""
    scope = _scope(db_engine, role=Role.DOCTOR, employee_number="CGH-D-0001")
    record_id = _one_id(
        db_engine,
        "SELECT mr.id FROM medical_records mr JOIN patients p ON p.id = mr.patient_id "
        "WHERE p.patient_number = 'CGH-P-0001' LIMIT 1",
    )
    assert record_id is not None
    with db_engine.connect() as conn:
        assert can_access_medical_record(conn, scope, record_id) is True


def test_doctor_a_cannot_access_patient_c_medical_record(db_engine):
    scope = _scope(db_engine, role=Role.DOCTOR, employee_number="CGH-D-0001")
    record_id = _one_id(
        db_engine,
        "SELECT mr.id FROM medical_records mr JOIN patients p ON p.id = mr.patient_id "
        "WHERE p.patient_number = 'CGH-P-0003' LIMIT 1",
    )
    assert record_id is not None
    with db_engine.connect() as conn:
        assert can_access_medical_record(conn, scope, record_id) is False


def test_doctor_b_can_access_patient_c_lab_report(db_engine):
    scope = _scope(db_engine, role=Role.DOCTOR, employee_number="CGH-D-0002")
    report_id = _one_id(
        db_engine,
        "SELECT lr.id FROM lab_reports lr JOIN patients p ON p.id = lr.patient_id "
        "WHERE p.patient_number = 'CGH-P-0003' LIMIT 1",
    )
    assert report_id is not None
    with db_engine.connect() as conn:
        assert can_access_lab_report(conn, scope, report_id) is True


def test_doctor_b_cannot_access_patient_a_prescription(db_engine):
    scope = _scope(db_engine, role=Role.DOCTOR, employee_number="CGH-D-0002")
    prescription_id = _one_id(
        db_engine,
        "SELECT pr.id FROM prescriptions pr JOIN patients p ON p.id = pr.patient_id "
        "WHERE p.patient_number = 'CGH-P-0001' LIMIT 1",
    )
    assert prescription_id is not None
    with db_engine.connect() as conn:
        assert can_access_prescription(conn, scope, prescription_id) is False


def test_unassigned_relationship_does_not_grant_access(db_engine):
    """A doctor and patient sharing a hospital is not enough - only an
    *active* doctor_patient_assignments row is."""
    scope = _scope(db_engine, role=Role.DOCTOR, employee_number="CGH-D-0002")  # Doctor B
    with db_engine.connect() as conn:
        # Doctor B has no assignment to Patient A at all.
        assert can_access_patient(conn, scope, _patient_id(db_engine, "CGH-P-0001")) is False


# --- Non-clinical roles: documented limitation, not implicit access -----


def test_nurse_has_no_patient_resource_access(db_engine):
    """NURSE has view_assigned_patients/view_vital_records in the permission
    seed, but Phase 2's schema has no nurse-patient assignment table, so the
    resource-relationship check always denies - see docs/AUTHORIZATION.md,
    "Limitations". This is a documented gap, not a bug: it means those
    permissions currently can't be exercised for any specific patient."""
    scope = _scope(db_engine, role=Role.NURSE)
    with db_engine.connect() as conn:
        assert can_access_patient(conn, scope, _patient_id(db_engine, "CGH-P-0001")) is False


def test_receptionist_has_no_clinical_patient_resource_access(db_engine):
    scope = _scope(db_engine, role=Role.RECEPTIONIST)
    with db_engine.connect() as conn:
        assert can_access_patient(conn, scope, _patient_id(db_engine, "CGH-P-0001")) is False


def test_hospital_admin_has_no_clinical_patient_resource_access(db_engine):
    """HOSPITAL_ADMIN's scope is operational (appointments), not clinical -
    see docs/AUTHORIZATION.md. (It's also moot in practice: the seed grants
    HOSPITAL_ADMIN no clinical permission in the first place.)"""
    scope = _scope(db_engine, role=Role.HOSPITAL_ADMIN)
    with db_engine.connect() as conn:
        assert can_access_patient(conn, scope, _patient_id(db_engine, "CGH-P-0001")) is False


# --- Appointment department/hospital scope for operational roles -------


def test_receptionist_can_access_appointment_in_own_department(db_engine):
    with db_engine.connect() as conn:
        department_id, appointment_id = conn.execute(
            text(
                "SELECT department_id, id FROM appointments WHERE department_id IS NOT NULL LIMIT 1"
            )
        ).one()
    scope = _scope(db_engine, role=Role.RECEPTIONIST, department_id=department_id)
    with db_engine.connect() as conn:
        assert can_access_appointment(conn, scope, appointment_id) is True


def test_receptionist_cannot_access_appointment_in_other_department(db_engine):
    with db_engine.connect() as conn:
        other_department_id = conn.execute(
            text("SELECT id FROM departments WHERE code = 'PHARM'")
        ).scalar_one()
        appointment_id = conn.execute(
            text(
                "SELECT a.id FROM appointments a JOIN departments d ON d.id = a.department_id "
                "WHERE d.code <> 'PHARM' LIMIT 1"
            )
        ).scalar_one()
    scope = _scope(db_engine, role=Role.RECEPTIONIST, department_id=other_department_id)
    with db_engine.connect() as conn:
        assert can_access_appointment(conn, scope, appointment_id) is False


def test_hospital_admin_can_access_appointment_in_any_department(db_engine):
    with db_engine.connect() as conn:
        appointment_id = conn.execute(text("SELECT id FROM appointments LIMIT 1")).scalar_one()
    scope = _scope(db_engine, role=Role.HOSPITAL_ADMIN, department_id=None)
    with db_engine.connect() as conn:
        assert can_access_appointment(conn, scope, appointment_id) is True


# --- Unknown / nonexistent resources ------------------------------------


def test_nonexistent_patient_denies_everyone(db_engine):
    scope = _scope(db_engine, role=Role.SUPER_ADMIN)
    with db_engine.connect() as conn:
        assert can_access_patient(conn, scope, uuid.uuid4()) is False


def test_nonexistent_medical_record_denies(db_engine):
    scope = _scope(db_engine, role=Role.DOCTOR, employee_number="CGH-D-0001")
    with db_engine.connect() as conn:
        assert can_access_medical_record(conn, scope, uuid.uuid4()) is False


# --- Hospital isolation: a second hospital created just for this test ---


@pytest.fixture
def second_hospital_patient(db_engine):
    """Creates a throwaway second hospital + patient so cross-hospital
    denial can be tested at the application-authorization level, not just
    the Phase 2 composite-FK level. Cleaned up unconditionally afterward."""
    with db_engine.connect() as conn:
        hospital_id = conn.execute(
            text(
                "INSERT INTO hospitals (name, code) VALUES ('Test Hospital B', 'TESTHOSP-B') "
                "RETURNING id"
            )
        ).scalar_one()
        patient_id = conn.execute(
            text(
                "INSERT INTO patients (hospital_id, patient_number) "
                "VALUES (:hospital_id, 'TESTHOSP-B-P-0001') RETURNING id"
            ),
            {"hospital_id": hospital_id},
        ).scalar_one()
        conn.commit()
    try:
        yield hospital_id, patient_id
    finally:
        with db_engine.connect() as conn:
            conn.execute(text("DELETE FROM patients WHERE id = :id"), {"id": patient_id})
            conn.execute(text("DELETE FROM hospitals WHERE id = :id"), {"id": hospital_id})
            conn.commit()


def test_hospital_a_doctor_cannot_access_hospital_b_patient(db_engine, second_hospital_patient):
    _hospital_b_id, patient_b_id = second_hospital_patient
    scope = _scope(db_engine, role=Role.DOCTOR, employee_number="CGH-D-0001")  # hospital A (CGH)
    with db_engine.connect() as conn:
        assert can_access_patient(conn, scope, patient_b_id) is False


def test_hospital_a_hospital_admin_cannot_reach_hospital_b_patient(db_engine, second_hospital_patient):
    """A Hospital A admin's scope.hospital_id can never match Hospital B's
    patient, so can_access_patient denies it regardless of role - this is
    the isolation check that matters; a full cross-hospital appointment
    fixture would need its own doctor and just re-tests the same FK Phase 2
    already covers."""
    _hospital_b_id, patient_b_id = second_hospital_patient
    scope = _scope(db_engine, role=Role.HOSPITAL_ADMIN)
    with db_engine.connect() as conn:
        assert can_access_patient(conn, scope, patient_b_id) is False


def test_superadmin_can_access_patient_in_any_hospital(db_engine, second_hospital_patient):
    _hospital_b_id, patient_b_id = second_hospital_patient
    scope = _scope(db_engine, role=Role.SUPER_ADMIN)
    with db_engine.connect() as conn:
        assert can_access_patient(conn, scope, patient_b_id) is True
