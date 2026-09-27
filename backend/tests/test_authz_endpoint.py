"""GET /api/authz/me through the full FastAPI dependency chain: JWT
verification (faked, as in test_auth_me.py) -> get_current_auth_user ->
require_permission -> resolve_user_scope. This is what proves
require_permission is actually wired correctly as a dependency, not just
that the underlying functions work in isolation.
"""

import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth.jwt_verifier import TokenClaims, get_token_verifier
from app.main import app

client = TestClient(app)


@dataclass
class _FakeVerifier:
    claims: TokenClaims

    def verify(self, token: str) -> TokenClaims:
        return self.claims


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_token_verifier, None)


def _auth_as(email: str, db_engine) -> None:
    with db_engine.connect() as conn:
        auth_user_id = conn.execute(
            text("SELECT auth_user_id FROM users WHERE email = :email"), {"email": email}
        ).scalar_one()
    app.dependency_overrides[get_token_verifier] = lambda: _FakeVerifier(
        TokenClaims(sub=str(auth_user_id), email=email, raw={})
    )


def _get_authz_me(**kwargs):
    return client.get("/api/authz/me", headers={"Authorization": "Bearer whatever"}, **kwargs)


def test_authz_me_without_token_is_401():
    response = client.get("/api/authz/me")
    assert response.status_code == 401


def test_authz_me_returns_role_hospital_and_permissions(db_engine):
    _auth_as("rohan.mehta@caresphere-demo.example", db_engine)
    response = _get_authz_me()
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "DOCTOR"
    assert "view_patient_medical_records" in body["permissions"]
    assert "manage_users" not in body["permissions"]
    assert "password" not in body


def test_authz_me_ignores_client_supplied_identity(db_engine):
    """Privilege-escalation check: query params claiming a different role/
    hospital/patient must have zero effect - the endpoint takes no such
    input in the first place, so the response must reflect only the
    server-resolved identity."""
    _auth_as("asha.verma@example-patient.example", db_engine)
    response = _get_authz_me(
        params={
            "role": "SUPER_ADMIN",
            "hospital_id": str(uuid.uuid4()),
            "patient_id": str(uuid.uuid4()),
            "permissions": "manage_users",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "PATIENT"
    assert "manage_users" not in body["permissions"]


def test_authz_me_denies_inactive_user(db_engine):
    with db_engine.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM users WHERE email = 'neha.joshi@caresphere-demo.example'")
        ).one()
        conn.execute(text("UPDATE users SET is_active = false WHERE id = :id"), {"id": row.id})
        conn.commit()
    try:
        _auth_as("neha.joshi@caresphere-demo.example", db_engine)
        response = _get_authz_me()
        assert response.status_code == 403
    finally:
        with db_engine.connect() as conn:
            conn.execute(text("UPDATE users SET is_active = true WHERE id = :id"), {"id": row.id})
            conn.commit()


def test_authz_me_denies_when_permission_mapping_removed(db_engine):
    """End-to-end proof (over real HTTP) that require_permission enforces a
    403 the moment the database says the role no longer has the permission,
    and a 200 again once it's restored - the same proof as
    test_permission_engine.py's mutation test, but through the actual
    FastAPI dependency rather than calling has_permission() directly."""
    with db_engine.connect() as conn:
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

    _auth_as("asha.verma@example-patient.example", db_engine)
    assert _get_authz_me().status_code == 200

    try:
        with db_engine.connect() as conn:
            conn.execute(
                text("DELETE FROM role_permissions WHERE role_id = :r AND permission_id = :p"),
                {"r": role_id, "p": permission_id},
            )
            conn.commit()
        assert _get_authz_me().status_code == 403
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

    assert _get_authz_me().status_code == 200
