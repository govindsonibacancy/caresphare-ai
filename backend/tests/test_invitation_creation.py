"""POST /api/admin/invitations and GET /api/admin/invitations: hospital
scope, role-escalation, department isolation, duplicate handling, and
client-manipulation (privilege escalation) tests.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app

client = TestClient(app)

HOSPITAL_ADMIN_EMAIL = "vikram.singh@caresphere-demo.example"
SUPER_ADMIN_EMAIL = "meera.kapoor@caresphere-demo.example"


def _create(payload, headers, **kwargs):
    return client.post("/api/admin/invitations", json=payload, headers=headers, **kwargs)


def _cleanup_invitation_by_email(db_engine, email: str) -> None:
    with db_engine.connect() as conn:
        conn.execute(text("DELETE FROM audit_logs WHERE metadata->>'email' = :email"), {"email": email})
        conn.execute(text("DELETE FROM employee_invitations WHERE lower(email) = lower(:email)"), {"email": email})
        conn.commit()


# --- allowed roles ---------------------------------------------------


@pytest.mark.parametrize("role", ["DOCTOR", "NURSE", "RECEPTIONIST", "STAFF"])
def test_hospital_admin_can_invite_allowed_roles(db_engine, auth_as, auth_headers, role):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    email = f"invite-{role.lower()}-{uuid.uuid4().hex[:8]}@example.com"
    try:
        response = _create(
            {"email": email, "first_name": "New", "last_name": role.title(), "role": role},
            auth_headers,
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["role"] == role
        assert body["status"] == "PENDING"
        assert "invitation_url" in body and "token=" in body["invitation_url"]
    finally:
        _cleanup_invitation_by_email(db_engine, email)


# --- role escalation ----------------------------------------------------


@pytest.mark.parametrize("role", ["PATIENT", "HOSPITAL_ADMIN", "SUPER_ADMIN"])
def test_hospital_admin_cannot_invite_privileged_roles(auth_as, auth_headers, role):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    email = f"escalate-{role.lower()}-{uuid.uuid4().hex[:8]}@example.com"
    response = _create(
        {"email": email, "first_name": "No", "last_name": "One", "role": role},
        auth_headers,
    )
    # PATIENT/HOSPITAL_ADMIN/SUPER_ADMIN aren't valid values of the Literal
    # type the request schema declares - Pydantic rejects them before any
    # business logic runs.
    assert response.status_code == 422


def test_invite_request_schema_has_no_escapable_fields(auth_as, auth_headers):
    """role_id/permission_ids/auth_user_id/invited_by_user_id/status/token
    aren't accepted at all - extra="forbid" makes submitting them a 422,
    not a silently-ignored no-op."""
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = _create(
        {
            "email": f"escalate-{uuid.uuid4().hex[:8]}@example.com",
            "first_name": "X",
            "last_name": "Y",
            "role": "DOCTOR",
            "role_id": str(uuid.uuid4()),
            "permission_ids": [str(uuid.uuid4())],
            "auth_user_id": str(uuid.uuid4()),
            "invited_by_user_id": str(uuid.uuid4()),
            "status": "ACCEPTED",
            "token": "attacker-chosen-token",
        },
        auth_headers,
    )
    assert response.status_code == 422


# --- hospital scope -------------------------------------------------------


def test_hospital_admin_cannot_invite_into_another_hospital(auth_as, auth_headers, second_hospital):
    auth_as(HOSPITAL_ADMIN_EMAIL)  # CGH admin
    response = _create(
        {
            "email": f"escape-{uuid.uuid4().hex[:8]}@example.com",
            "first_name": "Escape",
            "last_name": "Attempt",
            "role": "DOCTOR",
            "hospital_id": str(second_hospital["hospital_id"]),
        },
        auth_headers,
    )
    assert response.status_code == 403


def test_hospital_b_admin_can_invite_within_own_hospital(db_engine, auth_as, auth_headers, second_hospital):
    auth_as(second_hospital["admin_email"])
    email = f"hospb-{uuid.uuid4().hex[:8]}@example.com"
    try:
        response = _create(
            {"email": email, "first_name": "B", "last_name": "Employee", "role": "STAFF"},
            auth_headers,
        )
        assert response.status_code == 201
        assert response.json()["hospital_id"] == str(second_hospital["hospital_id"])
    finally:
        _cleanup_invitation_by_email(db_engine, email)


def test_super_admin_can_target_another_hospital_explicitly(db_engine, auth_as, auth_headers, second_hospital):
    auth_as(SUPER_ADMIN_EMAIL)
    email = f"superadmin-invite-{uuid.uuid4().hex[:8]}@example.com"
    try:
        response = _create(
            {
                "email": email,
                "first_name": "Cross",
                "last_name": "Hospital",
                "role": "NURSE",
                "hospital_id": str(second_hospital["hospital_id"]),
            },
            auth_headers,
        )
        assert response.status_code == 201
        assert response.json()["hospital_id"] == str(second_hospital["hospital_id"])
    finally:
        _cleanup_invitation_by_email(db_engine, email)


# --- department isolation -----------------------------------------------


def test_hospital_admin_cannot_use_another_hospitals_department(auth_as, auth_headers, second_hospital):
    auth_as(HOSPITAL_ADMIN_EMAIL)  # CGH admin
    response = _create(
        {
            "email": f"deptescape-{uuid.uuid4().hex[:8]}@example.com",
            "first_name": "Dept",
            "last_name": "Escape",
            "role": "DOCTOR",
            "department_id": str(second_hospital["department_id"]),
        },
        auth_headers,
    )
    assert response.status_code == 403


def test_hospital_admin_can_use_own_hospitals_department(db_engine, auth_as, auth_headers):
    with db_engine.connect() as conn:
        cardiology_id = conn.execute(text("SELECT id FROM departments WHERE code = 'CARD'")).scalar_one()
    auth_as(HOSPITAL_ADMIN_EMAIL)
    email = f"dept-ok-{uuid.uuid4().hex[:8]}@example.com"
    try:
        response = _create(
            {
                "email": email,
                "first_name": "Dept",
                "last_name": "Ok",
                "role": "DOCTOR",
                "department_id": str(cardiology_id),
            },
            auth_headers,
        )
        assert response.status_code == 201
        assert response.json()["department_id"] == str(cardiology_id)
    finally:
        _cleanup_invitation_by_email(db_engine, email)


# --- permission gate -------------------------------------------------------


def test_missing_permission_role_cannot_create_invitation(auth_as, auth_headers):
    # RECEPTIONIST has no manage_users permission in the seed.
    auth_as("sanjay.rao@caresphere-demo.example")
    response = _create(
        {
            "email": f"nope-{uuid.uuid4().hex[:8]}@example.com",
            "first_name": "No",
            "last_name": "Permission",
            "role": "DOCTOR",
        },
        auth_headers,
    )
    assert response.status_code == 403


def test_create_invitation_without_token_is_401():
    response = client.post(
        "/api/admin/invitations",
        json={"email": "x@example.com", "first_name": "X", "last_name": "Y", "role": "DOCTOR"},
    )
    assert response.status_code == 401


# --- duplicates ------------------------------------------------------------


def test_duplicate_pending_invitation_rejected(db_engine, auth_as, auth_headers):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    email = f"dup-{uuid.uuid4().hex[:8]}@example.com"
    payload = {"email": email, "first_name": "Dup", "last_name": "Licate", "role": "STAFF"}
    try:
        first = _create(payload, auth_headers)
        assert first.status_code == 201
        second = _create(payload, auth_headers)
        assert second.status_code == 409
    finally:
        _cleanup_invitation_by_email(db_engine, email)


def test_invitation_rejected_for_email_with_existing_user(auth_as, auth_headers):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = _create(
        {
            "email": "asha.verma@example-patient.example",  # seeded PATIENT's email
            "first_name": "Already",
            "last_name": "User",
            "role": "STAFF",
        },
        auth_headers,
    )
    assert response.status_code == 409


# --- listing -----------------------------------------------------------


def test_list_invitations_is_scoped_to_own_hospital(db_engine, auth_as, auth_headers, second_hospital):
    auth_as(second_hospital["admin_email"])
    email = f"listscope-{uuid.uuid4().hex[:8]}@example.com"
    try:
        create_response = _create(
            {"email": email, "first_name": "List", "last_name": "Scope", "role": "STAFF"}, auth_headers
        )
        assert create_response.status_code == 201

        # Same admin, listing with no hospital_id, only ever sees their own hospital's rows.
        list_response = client.get("/api/admin/invitations", headers=auth_headers)
        assert list_response.status_code == 200
        hospital_ids = {row["hospital_id"] for row in list_response.json()}
        assert hospital_ids == {str(second_hospital["hospital_id"])}
        emails = {row["email"] for row in list_response.json()}
        assert email in emails
    finally:
        _cleanup_invitation_by_email(db_engine, email)


def test_hospital_admin_cannot_list_another_hospital_via_query_param(auth_as, auth_headers, second_hospital):
    auth_as(HOSPITAL_ADMIN_EMAIL)  # CGH admin
    response = client.get(
        "/api/admin/invitations",
        params={"hospital_id": str(second_hospital["hospital_id"])},
        headers=auth_headers,
    )
    assert response.status_code == 403
