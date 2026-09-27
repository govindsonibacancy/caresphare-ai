"""GET /api/appointments and GET /api/appointments/{id}: role scoping
(patient own, doctor assigned, receptionist/staff department, hospital_admin
hospital-wide, nurse denied), filters, sorting, hospital isolation.
"""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app

client = TestClient(app)

DOCTOR_A_EMAIL = "rohan.mehta@caresphere-demo.example"
PATIENT_A_EMAIL = "asha.verma@example-patient.example"
RECEPTIONIST_EMAIL = "sanjay.rao@caresphere-demo.example"  # ADMIN department
STAFF_EMAIL = "neha.joshi@caresphere-demo.example"  # PHARM department
HOSPITAL_ADMIN_EMAIL = "vikram.singh@caresphere-demo.example"
SUPER_ADMIN_EMAIL = "meera.kapoor@caresphere-demo.example"
NURSE_EMAIL = "kavya.iyer@caresphere-demo.example"


def _appointment_id_for(db_engine, patient_number: str) -> uuid.UUID:
    with db_engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT a.id FROM appointments a JOIN patients p ON p.id = a.patient_id "
                "WHERE p.patient_number = :n LIMIT 1"
            ),
            {"n": patient_number},
        ).scalar_one()


def test_patient_lists_only_own_appointments(db_engine, auth_as, auth_headers):
    with db_engine.connect() as conn:
        patient_a_id = str(
            conn.execute(text("SELECT id FROM patients WHERE patient_number = 'CGH-P-0001'")).scalar_one()
        )
    auth_as(PATIENT_A_EMAIL)
    response = client.get("/api/appointments", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2  # Patient A has 2 seeded appointments
    assert all(a["patient_id"] == patient_a_id for a in body["items"])


def test_doctor_lists_only_assigned_patients_appointments(auth_as, auth_headers):
    auth_as(DOCTOR_A_EMAIL)
    response = client.get("/api/appointments", headers=auth_headers)
    assert response.status_code == 200
    # Doctor A is assigned to Patient A and Patient B, who have 4 seeded
    # appointments between them (Patient A: 2, Patient B: 1) - Patient C's
    # appointment must not appear.
    assert response.json()["total"] == 3


def test_receptionist_sees_only_own_department_appointments(auth_as, auth_headers):
    """RECEPTIONIST (Administration dept) sees appointments in their own
    department only - Cardiology/General Medicine appointments (all seeded
    ones) belong to other departments, so the list is empty."""
    auth_as(RECEPTIONIST_EMAIL)
    response = client.get("/api/appointments", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_hospital_admin_sees_all_hospital_appointments(auth_as, auth_headers):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get("/api/appointments", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["total"] == 4  # all 4 seeded CGH appointments, hospital-wide


def test_nurse_denied_appointments_endpoint(auth_as, auth_headers):
    """NURSE has none of view_own_appointments/create_appointments/
    manage_appointments in the seed."""
    auth_as(NURSE_EMAIL)
    response = client.get("/api/appointments", headers=auth_headers)
    assert response.status_code == 403


def test_super_admin_denied_appointments_endpoint(auth_as, auth_headers):
    """SUPER_ADMIN's seed permissions don't include manage_appointments
    either - see docs/SECURE_DATA_APIS.md, "Appointments"."""
    auth_as(SUPER_ADMIN_EMAIL)
    response = client.get("/api/appointments", headers=auth_headers)
    assert response.status_code == 403


# --- detail ---------------------------------------------------------------


def test_doctor_can_get_assigned_appointment(db_engine, auth_as, auth_headers):
    auth_as(DOCTOR_A_EMAIL)
    appointment_id = _appointment_id_for(db_engine, "CGH-P-0001")
    response = client.get(f"/api/appointments/{appointment_id}", headers=auth_headers)
    assert response.status_code == 200


def test_doctor_cannot_get_unassigned_appointment(db_engine, auth_as, auth_headers):
    auth_as(DOCTOR_A_EMAIL)
    appointment_id = _appointment_id_for(db_engine, "CGH-P-0003")
    response = client.get(f"/api/appointments/{appointment_id}", headers=auth_headers)
    assert response.status_code == 403


# --- filters: narrow, never expand ---------------------------------------


def test_patient_id_filter_narrows_within_scope(db_engine, auth_as, auth_headers):
    auth_as(DOCTOR_A_EMAIL)
    patient_a_id = str(
        db_engine.connect().execute(text("SELECT id FROM patients WHERE patient_number = 'CGH-P-0001'")).scalar_one()
    )
    response = client.get("/api/appointments", params={"patient_id": patient_a_id}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert all(a["patient_id"] == patient_a_id for a in response.json()["items"])


def test_patient_id_filter_for_unassigned_patient_returns_empty(db_engine, auth_as, auth_headers):
    """Doctor A filtering explicitly by Patient C's id (someone they are NOT
    assigned to) must not surface Patient C's appointment - the scope
    clause already excludes it before the filter is even applied."""
    auth_as(DOCTOR_A_EMAIL)
    with db_engine.connect() as conn:
        patient_c_id = str(conn.execute(text("SELECT id FROM patients WHERE patient_number = 'CGH-P-0003'")).scalar_one())
    response = client.get("/api/appointments", params={"patient_id": patient_c_id}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"items": [], "page": 1, "page_size": 20, "total": 0}


def test_status_filter(auth_as, auth_headers):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get("/api/appointments", params={"status": "NO_SHOW"}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["status"] == "NO_SHOW"


def test_invalid_status_filter_rejected(auth_as, auth_headers):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get("/api/appointments", params={"status": "DROP TABLE appointments"}, headers=auth_headers)
    assert response.status_code == 422


# --- sorting: explicit allowlist only ------------------------------------


def test_sort_by_appointment_date(auth_as, auth_headers):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get(
        "/api/appointments", params={"sort_by": "appointment_date", "descending": "false"}, headers=auth_headers
    )
    assert response.status_code == 200
    dates = [a["appointment_date"] for a in response.json()["items"]]
    assert dates == sorted(dates)


def test_sort_by_unknown_field_rejected(auth_as, auth_headers):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get(
        "/api/appointments",
        params={"sort_by": "reason; DROP TABLE appointments;--"},
        headers=auth_headers,
    )
    assert response.status_code == 422


# --- hospital isolation ---------------------------------------------------


def test_hospital_a_admin_cannot_get_hospital_b_appointment(auth_as, auth_headers, second_hospital_clinical_data):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get(
        f"/api/appointments/{second_hospital_clinical_data['appointment_id']}", headers=auth_headers
    )
    assert response.status_code == 403


def test_hospital_a_admin_list_excludes_hospital_b(auth_as, auth_headers, second_hospital_clinical_data):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get("/api/appointments", headers=auth_headers)
    assert response.json()["total"] == 4  # unchanged by Hospital B's appointment existing


# --- query manipulation ----------------------------------------------------


def test_department_id_override_does_not_grant_access(auth_as, auth_headers, second_hospital_clinical_data):
    """A receptionist passing another hospital's department_id as a filter
    must not see that hospital's appointments - the scope clause already
    pins hospital_id, so the filter (ANDed on top) can only ever narrow."""
    auth_as(RECEPTIONIST_EMAIL)
    response = client.get(
        "/api/appointments",
        params={"department_id": str(second_hospital_clinical_data["department_id"])},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["items"] == []
