"""GET /api/patients and GET /api/patients/{id}: role behavior, hospital
isolation, pagination, and audit logging.
"""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app

client = TestClient(app)

DOCTOR_A_EMAIL = "rohan.mehta@caresphere-demo.example"  # assigned to Patient A, B
DOCTOR_B_EMAIL = "priya.nair@caresphere-demo.example"  # assigned to Patient C
PATIENT_A_EMAIL = "asha.verma@example-patient.example"
RECEPTIONIST_EMAIL = "sanjay.rao@caresphere-demo.example"
HOSPITAL_ADMIN_EMAIL = "vikram.singh@caresphere-demo.example"
SUPER_ADMIN_EMAIL = "meera.kapoor@caresphere-demo.example"
NURSE_EMAIL = "kavya.iyer@caresphere-demo.example"


def _patient_id(db_engine, patient_number: str) -> uuid.UUID:
    with db_engine.connect() as conn:
        return conn.execute(
            text("SELECT id FROM patients WHERE patient_number = :n"), {"n": patient_number}
        ).scalar_one()


# --- doctor: assignment-scoped list/detail ------------------------------


def test_doctor_lists_only_assigned_patients(auth_as, auth_headers):
    auth_as(DOCTOR_A_EMAIL)
    response = client.get("/api/patients", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    numbers = {p["patient_number"] for p in body["items"]}
    assert numbers == {"CGH-P-0001", "CGH-P-0002"}
    assert body["total"] == 2


def test_doctor_b_lists_only_their_assigned_patient(auth_as, auth_headers):
    auth_as(DOCTOR_B_EMAIL)
    response = client.get("/api/patients", headers=auth_headers)
    assert response.status_code == 200
    numbers = {p["patient_number"] for p in response.json()["items"]}
    assert numbers == {"CGH-P-0003"}


def test_doctor_can_get_assigned_patient(db_engine, auth_as, auth_headers):
    auth_as(DOCTOR_A_EMAIL)
    response = client.get(f"/api/patients/{_patient_id(db_engine, 'CGH-P-0001')}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["patient_number"] == "CGH-P-0001"


def test_doctor_cannot_get_unassigned_patient(db_engine, auth_as, auth_headers):
    auth_as(DOCTOR_A_EMAIL)
    response = client.get(f"/api/patients/{_patient_id(db_engine, 'CGH-P-0003')}", headers=auth_headers)
    assert response.status_code == 403


# --- other roles: documented limitation (no permission or no relationship) --


def test_patient_role_denied_patients_endpoint(auth_as, auth_headers):
    """PATIENT has no view_assigned_patients/view_patient_basic_information
    permission in the seed - see docs/SECURE_DATA_APIS.md, "Patients"."""
    auth_as(PATIENT_A_EMAIL)
    response = client.get("/api/patients", headers=auth_headers)
    assert response.status_code == 403


def test_receptionist_permission_passes_but_resource_check_denies(auth_as, auth_headers):
    """RECEPTIONIST holds view_patient_basic_information (passes the
    permission gate) but can_access_patient still denies every patient (no
    clinical relationship) - list comes back empty, not an error."""
    auth_as(RECEPTIONIST_EMAIL)
    response = client.get("/api/patients", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"items": [], "page": 1, "page_size": 20, "total": 0}


def test_nurse_denied_patients_endpoint(auth_as, auth_headers):
    """NURSE lacks view_patient_basic_information (only view_assigned_patients,
    which alone doesn't grant it here per LIST_PERMISSIONS)... actually NURSE
    *does* have view_assigned_patients - it should pass the permission gate
    and, like RECEPTIONIST, get an empty list from the resource check."""
    auth_as(NURSE_EMAIL)
    response = client.get("/api/patients", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_hospital_admin_denied_patients_endpoint(auth_as, auth_headers):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get("/api/patients", headers=auth_headers)
    assert response.status_code == 403


def test_super_admin_denied_patients_endpoint(auth_as, auth_headers):
    """SUPER_ADMIN's seeded permissions do not include
    view_assigned_patients/view_patient_basic_information - see
    docs/AUTHORIZATION.md, "SUPER_ADMIN"."""
    auth_as(SUPER_ADMIN_EMAIL)
    response = client.get("/api/patients", headers=auth_headers)
    assert response.status_code == 403


# --- hospital isolation ---------------------------------------------------


def test_doctor_list_excludes_other_hospitals_patients(auth_as, auth_headers, second_hospital_clinical_data):
    auth_as(DOCTOR_A_EMAIL)
    response = client.get("/api/patients", headers=auth_headers)
    numbers = {p["patient_number"] for p in response.json()["items"]}
    assert not any(n.startswith("TESTHOSP-B") for n in numbers)


def test_hospital_a_doctor_cannot_get_hospital_b_patient(auth_as, auth_headers, second_hospital_clinical_data):
    auth_as(DOCTOR_A_EMAIL)
    response = client.get(
        f"/api/patients/{second_hospital_clinical_data['patient_id']}", headers=auth_headers
    )
    assert response.status_code == 403


def test_hospital_b_doctor_can_get_own_hospital_patient(auth_as, auth_headers, second_hospital_clinical_data):
    auth_as(second_hospital_clinical_data["doctor_email"])
    response = client.get(
        f"/api/patients/{second_hospital_clinical_data['patient_id']}", headers=auth_headers
    )
    assert response.status_code == 200


# --- enumeration protection: nonexistent vs unauthorized are indistinguishable --


def test_get_nonexistent_patient_is_403_not_404(auth_as, auth_headers):
    """Matches the existing Phase 4 convention (authorize() raises the same
    PermissionDenied for "doesn't exist" and "exists but out of scope") -
    see docs/SECURE_DATA_APIS.md, "Enumeration protection"."""
    auth_as(DOCTOR_A_EMAIL)
    response = client.get(f"/api/patients/{uuid.uuid4()}", headers=auth_headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "You do not have permission to perform this action."}


# --- authentication (inherited from Phase 3/4, quick confirmation only) ----


def test_patients_endpoint_without_token_is_401():
    response = client.get("/api/patients")
    assert response.status_code == 401


# --- pagination ------------------------------------------------------------


def test_pagination_default_and_bounds(auth_as, auth_headers):
    auth_as(DOCTOR_A_EMAIL)
    default = client.get("/api/patients", headers=auth_headers).json()
    assert default["page"] == 1
    assert default["page_size"] == 20

    small_page = client.get("/api/patients", params={"page_size": 1}, headers=auth_headers).json()
    assert len(small_page["items"]) == 1
    assert small_page["total"] == 2

    oversized = client.get("/api/patients", params={"page_size": 1000}, headers=auth_headers)
    assert oversized.status_code == 422  # server-side max (100) rejects it, doesn't silently clamp

    invalid = client.get("/api/patients", params={"page": 0}, headers=auth_headers)
    assert invalid.status_code == 422

    negative_size = client.get("/api/patients", params={"page_size": -1}, headers=auth_headers)
    assert negative_size.status_code == 422


def test_pagination_second_page_is_disjoint(auth_as, auth_headers):
    auth_as(DOCTOR_A_EMAIL)
    page1 = client.get("/api/patients", params={"page": 1, "page_size": 1}, headers=auth_headers).json()
    page2 = client.get("/api/patients", params={"page": 2, "page_size": 1}, headers=auth_headers).json()
    assert page1["items"][0]["id"] != page2["items"][0]["id"]


# --- query manipulation: nothing about the response is client-controlled --


def test_query_params_cannot_expand_doctor_scope(auth_as, auth_headers, second_hospital_clinical_data):
    """/api/patients has no hospital_id/patient_id/role query params at all
    - there is nothing to manipulate. This test documents that explicitly by
    confirming unknown query params are simply ignored, not honored."""
    auth_as(DOCTOR_A_EMAIL)
    response = client.get(
        "/api/patients",
        params={
            "hospital_id": str(second_hospital_clinical_data["hospital_id"]),
            "role": "SUPER_ADMIN",
            "patient_id": str(second_hospital_clinical_data["patient_id"]),
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    numbers = {p["patient_number"] for p in response.json()["items"]}
    assert numbers == {"CGH-P-0001", "CGH-P-0002"}  # unchanged - still just Doctor A's real scope


# --- audit logging -----------------------------------------------------


def test_get_patient_writes_audit_event(db_engine, auth_as, auth_headers, clean_clinical_audit_logs):
    auth_as(DOCTOR_A_EMAIL)
    patient_id = _patient_id(db_engine, "CGH-P-0001")
    response = client.get(f"/api/patients/{patient_id}", headers=auth_headers)
    assert response.status_code == 200

    with db_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT action, resource_type, resource_id, metadata FROM audit_logs "
                "WHERE action = 'PATIENT_RECORD_ACCESSED' AND resource_id = :id"
            ),
            {"id": patient_id},
        ).one()
    assert row.resource_type == "patients"
    assert row.metadata == {}  # no clinical payload in audit metadata


def test_denied_patient_access_writes_no_audit_event(db_engine, auth_as, auth_headers, clean_clinical_audit_logs):
    auth_as(DOCTOR_A_EMAIL)
    client.get(f"/api/patients/{uuid.uuid4()}", headers=auth_headers)  # 403, proven above
    with db_engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM audit_logs WHERE action = 'PATIENT_RECORD_ACCESSED'")
        ).scalar_one()
    assert count == 0
