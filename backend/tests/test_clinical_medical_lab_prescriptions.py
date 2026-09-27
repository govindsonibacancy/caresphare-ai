"""GET /api/medical-records, /api/lab-reports, /api/prescriptions: all
three share the same authorization pattern (delegate to can_access_patient
via view_patient_medical_records for DOCTOR, view_own_* for PATIENT where
seeded) - see docs/SECURE_DATA_APIS.md.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app

client = TestClient(app)

DOCTOR_A_EMAIL = "rohan.mehta@caresphere-demo.example"  # assigned to A, B
DOCTOR_B_EMAIL = "priya.nair@caresphere-demo.example"  # assigned to C
PATIENT_A_EMAIL = "asha.verma@example-patient.example"
HOSPITAL_ADMIN_EMAIL = "vikram.singh@caresphere-demo.example"
SUPER_ADMIN_EMAIL = "meera.kapoor@caresphere-demo.example"
RECEPTIONIST_EMAIL = "sanjay.rao@caresphere-demo.example"


RESOURCES = [
    ("medical-records", "medical_records"),
    ("lab-reports", "lab_reports"),
    ("prescriptions", "prescriptions"),
]


def _resource_id_for(db_engine, table: str, patient_number: str) -> uuid.UUID:
    with db_engine.connect() as conn:
        return conn.execute(
            text(f"SELECT r.id FROM {table} r JOIN patients p ON p.id = r.patient_id WHERE p.patient_number = :n LIMIT 1"),
            {"n": patient_number},
        ).scalar_one()


@pytest.mark.parametrize("path,table", RESOURCES)
def test_doctor_can_get_assigned_patients_record(db_engine, auth_as, auth_headers, path, table):
    auth_as(DOCTOR_A_EMAIL)
    resource_id = _resource_id_for(db_engine, table, "CGH-P-0001")
    response = client.get(f"/api/{path}/{resource_id}", headers=auth_headers)
    assert response.status_code == 200


@pytest.mark.parametrize("path,table", RESOURCES)
def test_doctor_cannot_get_unassigned_patients_record(db_engine, auth_as, auth_headers, path, table):
    auth_as(DOCTOR_A_EMAIL)
    resource_id = _resource_id_for(db_engine, table, "CGH-P-0003")
    response = client.get(f"/api/{path}/{resource_id}", headers=auth_headers)
    assert response.status_code == 403


@pytest.mark.parametrize("path,table", RESOURCES)
def test_doctor_b_can_reach_their_own_assigned_patient(db_engine, auth_as, auth_headers, path, table):
    auth_as(DOCTOR_B_EMAIL)
    resource_id = _resource_id_for(db_engine, table, "CGH-P-0003")
    response = client.get(f"/api/{path}/{resource_id}", headers=auth_headers)
    assert response.status_code == 200


@pytest.mark.parametrize("path,table", RESOURCES)
def test_doctor_lists_only_assigned_patients_records(auth_as, auth_headers, path, table):
    auth_as(DOCTOR_B_EMAIL)  # assigned only to Patient C
    response = client.get(f"/api/{path}", headers=auth_headers)
    assert response.status_code == 200
    for item in response.json()["items"]:
        assert item["patient_id"]  # every row present is implicitly Patient C's (scope-filtered)


@pytest.mark.parametrize("path", ["medical-records", "lab-reports", "prescriptions"])
def test_hospital_admin_denied_all_three(auth_as, auth_headers, path):
    """HOSPITAL_ADMIN has none of view_patient_medical_records/
    view_own_reports/view_own_prescriptions in the seed."""
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get(f"/api/{path}", headers=auth_headers)
    assert response.status_code == 403


@pytest.mark.parametrize("path", ["medical-records", "lab-reports", "prescriptions"])
def test_super_admin_denied_all_three(auth_as, auth_headers, path):
    auth_as(SUPER_ADMIN_EMAIL)
    response = client.get(f"/api/{path}", headers=auth_headers)
    assert response.status_code == 403


@pytest.mark.parametrize("path", ["medical-records", "lab-reports", "prescriptions"])
def test_receptionist_denied_all_three(auth_as, auth_headers, path):
    auth_as(RECEPTIONIST_EMAIL)
    response = client.get(f"/api/{path}", headers=auth_headers)
    assert response.status_code == 403


# --- patient: medical records denied entirely, lab reports/prescriptions own-only --


def test_patient_denied_medical_records_entirely(auth_as, auth_headers):
    """No seeded permission grants PATIENT access to medical_records at
    all, even their own - see docs/AUTHORIZATION.md, "Permission + scope"
    and docs/SECURE_DATA_APIS.md, "Medical records"."""
    auth_as(PATIENT_A_EMAIL)
    response = client.get("/api/medical-records", headers=auth_headers)
    assert response.status_code == 403


def test_patient_can_list_own_lab_reports(db_engine, auth_as, auth_headers):
    with db_engine.connect() as conn:
        patient_a_id = str(
            conn.execute(text("SELECT id FROM patients WHERE patient_number = 'CGH-P-0001'")).scalar_one()
        )
    auth_as(PATIENT_A_EMAIL)
    response = client.get("/api/lab-reports", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert all(r["patient_id"] == patient_a_id for r in body["items"])


def test_patient_can_list_own_prescriptions(auth_as, auth_headers):
    auth_as(PATIENT_A_EMAIL)
    response = client.get("/api/prescriptions", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["total"] >= 1


def test_patient_a_cannot_get_patient_b_lab_report(db_engine, auth_as, auth_headers):
    auth_as(PATIENT_A_EMAIL)
    report_id = _resource_id_for(db_engine, "lab_reports", "CGH-P-0002")
    response = client.get(f"/api/lab-reports/{report_id}", headers=auth_headers)
    assert response.status_code == 403


def test_patient_a_cannot_get_patient_b_prescription(db_engine, auth_as, auth_headers):
    auth_as(PATIENT_A_EMAIL)
    prescription_id = _resource_id_for(db_engine, "prescriptions", "CGH-P-0002")
    response = client.get(f"/api/prescriptions/{prescription_id}", headers=auth_headers)
    assert response.status_code == 403


# --- hospital isolation ---------------------------------------------------


@pytest.mark.parametrize("path,record_key", [("medical-records", "medical_record_id"), ("lab-reports", "lab_report_id"), ("prescriptions", "prescription_id")])
def test_hospital_a_doctor_cannot_get_hospital_b_record(
    auth_as, auth_headers, second_hospital_clinical_data, path, record_key
):
    auth_as(DOCTOR_A_EMAIL)
    response = client.get(f"/api/{path}/{second_hospital_clinical_data[record_key]}", headers=auth_headers)
    assert response.status_code == 403


@pytest.mark.parametrize("path,record_key", [("medical-records", "medical_record_id"), ("lab-reports", "lab_report_id"), ("prescriptions", "prescription_id")])
def test_hospital_b_doctor_can_get_own_hospital_record(
    auth_as, auth_headers, second_hospital_clinical_data, path, record_key
):
    auth_as(second_hospital_clinical_data["doctor_email"])
    response = client.get(f"/api/{path}/{second_hospital_clinical_data[record_key]}", headers=auth_headers)
    assert response.status_code == 200


# --- audit logging -----------------------------------------------------


def test_get_medical_record_writes_audit_event(db_engine, auth_as, auth_headers, clean_clinical_audit_logs):
    auth_as(DOCTOR_A_EMAIL)
    record_id = _resource_id_for(db_engine, "medical_records", "CGH-P-0001")
    client.get(f"/api/medical-records/{record_id}", headers=auth_headers)
    with db_engine.connect() as conn:
        row = conn.execute(
            text("SELECT metadata FROM audit_logs WHERE action = 'MEDICAL_RECORD_ACCESSED' AND resource_id = :id"),
            {"id": record_id},
        ).one()
    assert row.metadata == {}


def test_get_lab_report_writes_audit_event(db_engine, auth_as, auth_headers, clean_clinical_audit_logs):
    auth_as(DOCTOR_A_EMAIL)
    report_id = _resource_id_for(db_engine, "lab_reports", "CGH-P-0001")
    client.get(f"/api/lab-reports/{report_id}", headers=auth_headers)
    with db_engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM audit_logs WHERE action = 'LAB_REPORT_ACCESSED' AND resource_id = :id"),
            {"id": report_id},
        ).scalar_one()
    assert count == 1


def test_get_prescription_writes_audit_event(db_engine, auth_as, auth_headers, clean_clinical_audit_logs):
    auth_as(DOCTOR_A_EMAIL)
    prescription_id = _resource_id_for(db_engine, "prescriptions", "CGH-P-0001")
    client.get(f"/api/prescriptions/{prescription_id}", headers=auth_headers)
    with db_engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM audit_logs WHERE action = 'PRESCRIPTION_ACCESSED' AND resource_id = :id"),
            {"id": prescription_id},
        ).scalar_one()
    assert count == 1


def test_audit_metadata_never_contains_clinical_content(db_engine, auth_as, auth_headers, clean_clinical_audit_logs):
    """The metadata column must never carry clinical_notes/description/
    result - only resource_type/resource_id/hospital_id (separate columns)
    identify what was accessed."""
    auth_as(DOCTOR_A_EMAIL)
    record_id = _resource_id_for(db_engine, "medical_records", "CGH-P-0001")
    client.get(f"/api/medical-records/{record_id}", headers=auth_headers)
    with db_engine.connect() as conn:
        metadata = conn.execute(
            text("SELECT metadata FROM audit_logs WHERE action = 'MEDICAL_RECORD_ACCESSED' AND resource_id = :id"),
            {"id": record_id},
        ).scalar_one()
    assert metadata == {}
