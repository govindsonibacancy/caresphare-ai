"""Cross-cutting security tests for the Phase 6 clinical APIs: authentication
edge cases (proving the new endpoints inherit Phase 3/4's JWT verification
and active-account checks rather than reimplementing them) and additional
query-parameter privilege-escalation attempts not already covered in the
per-resource test files.
"""

import uuid
from dataclasses import dataclass

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth.jwt_verifier import TokenClaims, TokenVerificationError, get_token_verifier
from app.main import app

client = TestClient(app)

DOCTOR_A_EMAIL = "rohan.mehta@caresphere-demo.example"
STAFF_EMAIL = "neha.joshi@caresphere-demo.example"


@dataclass
class _FakeVerifier:
    claims: TokenClaims | None = None
    error: Exception | None = None

    def verify(self, token: str) -> TokenClaims:
        if self.error is not None:
            raise self.error
        assert self.claims is not None
        return self.claims


def _override(verifier: _FakeVerifier) -> None:
    app.dependency_overrides[get_token_verifier] = lambda: verifier


def teardown_function() -> None:
    app.dependency_overrides.pop(get_token_verifier, None)


# --- authentication: inherited from Phase 3/4, exercised through a Phase 6 endpoint --


def test_invalid_jwt_is_401_on_clinical_endpoint():
    _override(_FakeVerifier(error=TokenVerificationError("bad signature")))
    response = client.get("/api/patients", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


def test_expired_jwt_is_401_on_clinical_endpoint():
    _override(_FakeVerifier(error=TokenVerificationError("token is expired")))
    response = client.get("/api/appointments", headers={"Authorization": "Bearer expired"})
    assert response.status_code == 401


def test_unknown_application_user_is_403_on_clinical_endpoint():
    _override(_FakeVerifier(claims=TokenClaims(sub=str(uuid.uuid4()), email="ghost@example.com", raw={})))
    response = client.get("/api/doctors", headers={"Authorization": "Bearer whatever"})
    assert response.status_code == 403


def test_inactive_application_user_is_403_on_clinical_endpoint(db_engine):
    with db_engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, auth_user_id FROM users WHERE email = :email"), {"email": STAFF_EMAIL}
        ).one()
        conn.execute(text("UPDATE users SET is_active = false WHERE id = :id"), {"id": row.id})
        conn.commit()
    try:
        _override(_FakeVerifier(claims=TokenClaims(sub=str(row.auth_user_id), email=STAFF_EMAIL, raw={})))
        response = client.get("/api/doctors", headers={"Authorization": "Bearer whatever"})
        assert response.status_code == 403
    finally:
        with db_engine.connect() as conn:
            conn.execute(text("UPDATE users SET is_active = true WHERE id = :id"), {"id": row.id})
            conn.commit()


# --- query manipulation: doctor_id/staff_id overrides ---------------------


def test_doctor_id_filter_for_another_doctor_returns_only_own_scope(db_engine, auth_as, auth_headers):
    """A receptionist (hospital-wide within their department for
    appointments) filtering by a doctor_id from another department must
    still only see rows already within their department scope - the filter
    narrows, it cannot pull in appointments the base scope excluded."""
    with db_engine.connect() as conn:
        other_doctor_id = str(
            conn.execute(text("SELECT id FROM doctors WHERE employee_number = 'CGH-D-0002'")).scalar_one()
        )
    auth_as("sanjay.rao@caresphere-demo.example")  # RECEPTIONIST, Administration dept
    response = client.get("/api/appointments", params={"doctor_id": other_doctor_id}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["items"] == []  # Administration dept has no appointments at all


def test_medical_record_doctor_id_filter_cannot_surface_unassigned_patient(db_engine, auth_as, auth_headers):
    """Doctor A filtering medical records by Doctor B's id (hoping to see
    records authored by/for Doctor B's patients) still only gets records for
    patients Doctor A is themselves assigned to."""
    with db_engine.connect() as conn:
        doctor_b_id = str(
            conn.execute(text("SELECT id FROM doctors WHERE employee_number = 'CGH-D-0002'")).scalar_one()
        )
    auth_as(DOCTOR_A_EMAIL)
    response = client.get("/api/medical-records", params={"doctor_id": doctor_b_id}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_appointment_id_path_parameter_cannot_be_guessed_into_access(auth_as, auth_headers):
    """A random (nonexistent) appointment id behaves identically to a real
    but unauthorized one - 403 either way, no distinguishing signal."""
    auth_as(DOCTOR_A_EMAIL)
    response = client.get(f"/api/appointments/{uuid.uuid4()}", headers=auth_headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "You do not have permission to perform this action."}
