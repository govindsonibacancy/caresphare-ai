"""POST /api/auth/register, with a fake SupabaseAdminClient standing in for
the real GoTrue Admin API call (see app/auth/supabase_admin.py) - no real
Supabase project or credentials are needed to run these.
"""

import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth.supabase_admin import SupabaseUser, get_supabase_admin_client
from app.main import app

client = TestClient(app)


@dataclass
class _FakeAdminClient:
    user: SupabaseUser | None

    def get_user_by_id(self, auth_user_id: str) -> SupabaseUser | None:
        return self.user


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_supabase_admin_client, None)


def _cleanup_user(db_engine, auth_user_id: str) -> None:
    with db_engine.connect() as conn:
        conn.execute(
            text(
                "DELETE FROM audit_logs WHERE resource_id IN (SELECT id FROM users WHERE auth_user_id = :aid)"
            ),
            {"aid": auth_user_id},
        )
        conn.execute(
            text(
                "DELETE FROM patients WHERE user_id IN (SELECT id FROM users WHERE auth_user_id = :aid)"
            ),
            {"aid": auth_user_id},
        )
        conn.execute(text("DELETE FROM users WHERE auth_user_id = :aid"), {"aid": auth_user_id})
        conn.commit()


def test_registration_creates_patient_user_and_profile(db_engine):
    new_auth_id = str(uuid.uuid4())
    email = f"newpatient-{new_auth_id[:8]}@example.com"
    app.dependency_overrides[get_supabase_admin_client] = lambda: _FakeAdminClient(
        SupabaseUser(id=new_auth_id, email=email, email_confirmed_at=None)
    )

    try:
        response = client.post(
            "/api/auth/register",
            json={"auth_user_id": new_auth_id, "email": email, "first_name": "New", "last_name": "Patient"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["role"] == "PATIENT"
        assert body["email"] == email
        assert "password" not in body

        with db_engine.connect() as conn:
            user_row = conn.execute(
                text("SELECT id, role_id, hospital_id FROM users WHERE auth_user_id = :aid"),
                {"aid": new_auth_id},
            ).one()
            role_name = conn.execute(
                text("SELECT name FROM roles WHERE id = :rid"), {"rid": user_row.role_id}
            ).scalar_one()
            hospital_code = conn.execute(
                text("SELECT code FROM hospitals WHERE id = :hid"), {"hid": user_row.hospital_id}
            ).scalar_one()
            patient_count = conn.execute(
                text("SELECT count(*) FROM patients WHERE user_id = :uid"), {"uid": user_row.id}
            ).scalar_one()
            audit_count = conn.execute(
                text(
                    "SELECT count(*) FROM audit_logs WHERE action = 'USER_CREATED' AND resource_id = :uid"
                ),
                {"uid": user_row.id},
            ).scalar_one()

        assert role_name == "PATIENT"
        assert hospital_code == "CGH"
        assert patient_count == 1
        assert audit_count == 1
    finally:
        _cleanup_user(db_engine, new_auth_id)


def test_registration_rejects_unverifiable_identity(db_engine):
    new_auth_id = str(uuid.uuid4())
    app.dependency_overrides[get_supabase_admin_client] = lambda: _FakeAdminClient(user=None)

    response = client.post(
        "/api/auth/register",
        json={"auth_user_id": new_auth_id, "email": "nope@example.com", "first_name": "No", "last_name": "One"},
    )
    assert response.status_code == 400


def test_registration_rejects_email_mismatch(db_engine):
    new_auth_id = str(uuid.uuid4())
    app.dependency_overrides[get_supabase_admin_client] = lambda: _FakeAdminClient(
        SupabaseUser(id=new_auth_id, email="real@example.com", email_confirmed_at=None)
    )

    response = client.post(
        "/api/auth/register",
        json={
            "auth_user_id": new_auth_id,
            "email": "spoofed@example.com",
            "first_name": "X",
            "last_name": "Y",
        },
    )
    assert response.status_code == 400


def test_registration_rejects_duplicate_identity(db_engine):
    with db_engine.connect() as conn:
        auth_user_id, email = conn.execute(
            text("SELECT auth_user_id, email FROM users WHERE email = 'asha.verma@example-patient.example'")
        ).one()

    app.dependency_overrides[get_supabase_admin_client] = lambda: _FakeAdminClient(
        SupabaseUser(id=str(auth_user_id), email=email, email_confirmed_at=None)
    )

    response = client.post(
        "/api/auth/register",
        json={"auth_user_id": str(auth_user_id), "email": email, "first_name": "Asha", "last_name": "Verma"},
    )
    assert response.status_code == 409


# --- security: privilege escalation ----------------------------------


def test_registration_request_rejects_unexpected_privileged_fields():
    """A submitted `role`/`hospital_id` must be rejected outright, not
    silently dropped or honored - see RegisterRequest's `extra="forbid"`."""
    response = client.post(
        "/api/auth/register",
        json={
            "auth_user_id": str(uuid.uuid4()),
            "email": "escalate@example.com",
            "first_name": "Would",
            "last_name": "BeAdmin",
            "role": "HOSPITAL_ADMIN",
        },
    )
    assert response.status_code == 422


def test_registration_request_rejects_hospital_id_field():
    response = client.post(
        "/api/auth/register",
        json={
            "auth_user_id": str(uuid.uuid4()),
            "email": "escalate2@example.com",
            "first_name": "Would",
            "last_name": "PickHospital",
            "hospital_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert response.status_code == 422


def test_no_employee_self_registration_routes_exist():
    """The actual property this proves: no employee-role self-registration
    endpoint exists, and the auth-prefixed surface is exactly the two
    identity endpoints Phase 3/4 defined - it deliberately does not enumerate
    the whole app's routes (Phase 6's clinical routers are unrelated to this
    concern and would make this assertion churn every phase)."""
    openapi = client.get("/openapi.json").json()
    paths = set(openapi["paths"].keys())
    forbidden = {
        "/api/auth/register-doctor",
        "/api/auth/register-admin",
        "/api/auth/register-nurse",
        "/api/auth/register-staff",
        "/api/auth/register-employee",
    }
    assert not (paths & forbidden)
    auth_paths = {p for p in paths if p.startswith("/api/auth/")}
    assert auth_paths == {"/api/auth/register", "/api/auth/me"}
