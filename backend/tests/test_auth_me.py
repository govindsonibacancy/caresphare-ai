"""GET /api/auth/me, exercised through the FastAPI dependency chain with a
fake TokenVerifier (app.dependency_overrides) rather than real Supabase
tokens - see test_jwt_verifier.py for the real signature/issuer/audience/
expiration checks.
"""

import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth.jwt_verifier import TokenClaims, TokenVerificationError, get_token_verifier
from app.main import app

client = TestClient(app)


@dataclass
class _FakeVerifier:
    claims: TokenClaims | None = None
    error: Exception | None = None

    def verify(self, token: str) -> TokenClaims:
        if self.error is not None:
            raise self.error
        assert self.claims is not None
        return self.claims


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_token_verifier, None)


def _override(verifier: _FakeVerifier) -> None:
    app.dependency_overrides[get_token_verifier] = lambda: verifier


def test_me_without_token_is_401():
    response = client.get("/api/auth/me")
    assert response.status_code == 401


def test_me_with_invalid_token_is_401():
    _override(_FakeVerifier(error=TokenVerificationError("bad signature")))
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


def test_me_with_expired_token_is_401():
    _override(_FakeVerifier(error=TokenVerificationError("token is expired")))
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer expired"})
    assert response.status_code == 401


def test_me_with_unknown_identity_is_denied(db_engine):
    _override(_FakeVerifier(claims=TokenClaims(sub=str(uuid.uuid4()), email="ghost@example.com", raw={})))
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer whatever"})
    assert response.status_code == 403


def test_me_with_known_active_user_succeeds(db_engine):
    with db_engine.connect() as conn:
        auth_user_id = conn.execute(
            text("SELECT auth_user_id FROM users WHERE email = 'asha.verma@example-patient.example'")
        ).scalar_one()

    _override(_FakeVerifier(claims=TokenClaims(sub=str(auth_user_id), email="asha.verma@example-patient.example", raw={})))
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer whatever"})
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "asha.verma@example-patient.example"
    assert body["role"] == "PATIENT"
    assert "password" not in body
    assert "access_token" not in body


def test_me_with_inactive_user_is_denied(db_engine):
    with db_engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, auth_user_id FROM users WHERE email = 'neha.joshi@caresphere-demo.example'")
        ).one()
        conn.execute(text("UPDATE users SET is_active = false WHERE id = :id"), {"id": row.id})
        conn.commit()

    try:
        _override(_FakeVerifier(claims=TokenClaims(sub=str(row.auth_user_id), email="neha.joshi@caresphere-demo.example", raw={})))
        response = client.get("/api/auth/me", headers={"Authorization": "Bearer whatever"})
        assert response.status_code == 403
    finally:
        with db_engine.connect() as conn:
            conn.execute(text("UPDATE users SET is_active = true WHERE id = :id"), {"id": row.id})
            conn.commit()
