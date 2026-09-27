"""POST /api/admin/invitations/{id}/revoke: authorization scope and status
transitions not already covered by test_invitation_acceptance.py's
accept-after-revoke test.
"""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app

client = TestClient(app)

HOSPITAL_ADMIN_EMAIL = "vikram.singh@caresphere-demo.example"


def _create(auth_as, auth_headers, *, role="STAFF"):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    email = f"revoke-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/admin/invitations",
        json={"email": email, "first_name": "Revoke", "last_name": "Test", "role": role},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _cleanup(db_engine, invitation_id: str) -> None:
    with db_engine.connect() as conn:
        conn.execute(
            text("DELETE FROM audit_logs WHERE resource_type = 'employee_invitations' AND resource_id = :id"),
            {"id": invitation_id},
        )
        conn.execute(text("DELETE FROM employee_invitations WHERE id = :id"), {"id": invitation_id})
        conn.commit()


def test_revoke_marks_invitation_revoked(db_engine, auth_as, auth_headers):
    invitation = _create(auth_as, auth_headers)
    try:
        auth_as(HOSPITAL_ADMIN_EMAIL)
        response = client.post(f"/api/admin/invitations/{invitation['id']}/revoke", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["status"] == "REVOKED"
    finally:
        _cleanup(db_engine, invitation["id"])


def test_revoke_is_idempotent_refused_on_already_revoked(db_engine, auth_as, auth_headers):
    invitation = _create(auth_as, auth_headers)
    try:
        auth_as(HOSPITAL_ADMIN_EMAIL)
        first = client.post(f"/api/admin/invitations/{invitation['id']}/revoke", headers=auth_headers)
        assert first.status_code == 200
        second = client.post(f"/api/admin/invitations/{invitation['id']}/revoke", headers=auth_headers)
        assert second.status_code == 409
    finally:
        _cleanup(db_engine, invitation["id"])


def test_revoke_preserves_invitation_history(db_engine, auth_as, auth_headers):
    """Revocation updates status, it never deletes the row - auditability."""
    invitation = _create(auth_as, auth_headers)
    try:
        auth_as(HOSPITAL_ADMIN_EMAIL)
        client.post(f"/api/admin/invitations/{invitation['id']}/revoke", headers=auth_headers)
        with db_engine.connect() as conn:
            row = conn.execute(
                text("SELECT status, email FROM employee_invitations WHERE id = :id"), {"id": invitation["id"]}
            ).one()
        assert row.status == "REVOKED"
        assert row.email == invitation["email"]
    finally:
        _cleanup(db_engine, invitation["id"])


def test_hospital_b_admin_cannot_revoke_hospital_a_invitation(db_engine, auth_as, auth_headers, second_hospital):
    invitation = _create(auth_as, auth_headers)  # created by CGH (hospital A) admin
    try:
        auth_as(second_hospital["admin_email"])
        response = client.post(f"/api/admin/invitations/{invitation['id']}/revoke", headers=auth_headers)
        assert response.status_code == 403

        with db_engine.connect() as conn:
            status = conn.execute(
                text("SELECT status FROM employee_invitations WHERE id = :id"), {"id": invitation["id"]}
            ).scalar_one()
        assert status == "PENDING"  # unaffected by the denied attempt
    finally:
        _cleanup(db_engine, invitation["id"])


def test_revoke_without_permission_denied(db_engine, auth_as, auth_headers):
    invitation = _create(auth_as, auth_headers)
    try:
        auth_as("sanjay.rao@caresphere-demo.example")  # RECEPTIONIST, no manage_users
        response = client.post(f"/api/admin/invitations/{invitation['id']}/revoke", headers=auth_headers)
        assert response.status_code == 403
    finally:
        _cleanup(db_engine, invitation["id"])


def test_revoke_unknown_invitation_404(auth_as, auth_headers):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.post(f"/api/admin/invitations/{uuid.uuid4()}/revoke", headers=auth_headers)
    assert response.status_code == 404
