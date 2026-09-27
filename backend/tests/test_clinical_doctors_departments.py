"""GET /api/doctors and GET /api/departments: hospital-scoped directory
resources gated on view_hospital_operations, distinct from any
patient-relationship rule.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app

client = TestClient(app)

RECEPTIONIST_EMAIL = "sanjay.rao@caresphere-demo.example"
STAFF_EMAIL = "neha.joshi@caresphere-demo.example"
HOSPITAL_ADMIN_EMAIL = "vikram.singh@caresphere-demo.example"
SUPER_ADMIN_EMAIL = "meera.kapoor@caresphere-demo.example"
DOCTOR_A_EMAIL = "rohan.mehta@caresphere-demo.example"
NURSE_EMAIL = "kavya.iyer@caresphere-demo.example"
PATIENT_A_EMAIL = "asha.verma@example-patient.example"


@pytest.mark.parametrize("path", ["doctors", "departments"])
@pytest.mark.parametrize("email", [RECEPTIONIST_EMAIL, STAFF_EMAIL, HOSPITAL_ADMIN_EMAIL, SUPER_ADMIN_EMAIL])
def test_roles_with_view_hospital_operations_can_list(auth_as, auth_headers, path, email):
    auth_as(email)
    response = client.get(f"/api/{path}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["total"] >= 1


@pytest.mark.parametrize("path", ["doctors", "departments"])
@pytest.mark.parametrize("email", [DOCTOR_A_EMAIL, NURSE_EMAIL, PATIENT_A_EMAIL])
def test_roles_without_view_hospital_operations_denied(auth_as, auth_headers, path, email):
    """DOCTOR/NURSE/PATIENT don't hold view_hospital_operations in the seed -
    see docs/SECURE_DATA_APIS.md, "Doctors" / "Departments"."""
    auth_as(email)
    response = client.get(f"/api/{path}", headers=auth_headers)
    assert response.status_code == 403


def test_departments_list_returns_seeded_departments(auth_as, auth_headers):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get("/api/departments", headers=auth_headers)
    assert response.status_code == 200
    codes = {d["code"] for d in response.json()["items"]}
    assert "CARD" in codes
    assert "ER" in codes


def test_doctors_list_department_filter(db_engine, auth_as, auth_headers):
    with db_engine.connect() as conn:
        cardiology_id = str(conn.execute(text("SELECT id FROM departments WHERE code = 'CARD'")).scalar_one())
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get("/api/doctors", params={"department_id": cardiology_id}, headers=auth_headers)
    assert response.status_code == 200
    assert all(d["department_id"] == cardiology_id for d in response.json()["items"])


# --- hospital isolation ---------------------------------------------------


def test_hospital_a_admin_list_excludes_hospital_b_doctor(auth_as, auth_headers, second_hospital_clinical_data):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get("/api/doctors", headers=auth_headers)
    doctor_ids = {d["id"] for d in response.json()["items"]}
    assert str(second_hospital_clinical_data["doctor_id"]) not in doctor_ids


def test_hospital_a_admin_cannot_get_hospital_b_doctor(auth_as, auth_headers, second_hospital_clinical_data):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get(f"/api/doctors/{second_hospital_clinical_data['doctor_id']}", headers=auth_headers)
    assert response.status_code == 403


def test_hospital_a_admin_cannot_get_hospital_b_department(auth_as, auth_headers, second_hospital_clinical_data):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get(f"/api/departments/{second_hospital_clinical_data['department_id']}", headers=auth_headers)
    assert response.status_code == 403


def test_super_admin_can_get_any_hospital_doctor(auth_as, auth_headers, second_hospital_clinical_data):
    auth_as(SUPER_ADMIN_EMAIL)
    response = client.get(f"/api/doctors/{second_hospital_clinical_data['doctor_id']}", headers=auth_headers)
    assert response.status_code == 200


def test_get_nonexistent_doctor_is_403(auth_as, auth_headers):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get(f"/api/doctors/{uuid.uuid4()}", headers=auth_headers)
    assert response.status_code == 403


def test_doctors_endpoint_without_token_is_401():
    response = client.get("/api/doctors")
    assert response.status_code == 401
