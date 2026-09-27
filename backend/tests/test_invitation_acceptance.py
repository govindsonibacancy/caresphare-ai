"""GET /api/invitations/{token} and POST /api/invitations/{token}/accept:
token security, replay protection, concurrent acceptance, and employee
identity provisioning (doctor/nurse/receptionist/staff).
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth.supabase_admin import SupabaseAdminError, SupabaseUser, get_supabase_admin_client
from app.main import app

client = TestClient(app)

HOSPITAL_ADMIN_EMAIL = "vikram.singh@caresphere-demo.example"


class _FakeAdminClient:
    """Maps auth_user_id -> SupabaseUser, so a test can register multiple
    distinct fake identities (needed for the concurrent-acceptance test,
    where two different Supabase accounts race for one invitation)."""

    def __init__(self, users: dict[str, SupabaseUser]):
        self._users = users

    def get_user_by_id(self, auth_user_id: str) -> SupabaseUser | None:
        return self._users.get(auth_user_id)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_supabase_admin_client, None)


def _extract_token(invitation_url: str) -> str:
    return parse_qs(urlparse(invitation_url).query)["token"][0]


def _create_invitation(auth_as, auth_headers, *, role: str, department_id: str | None = None) -> tuple[dict, str]:
    auth_as(HOSPITAL_ADMIN_EMAIL)
    payload = {
        "email": f"accept-{role.lower()}-{uuid.uuid4().hex[:8]}@example.com",
        "first_name": "Accept",
        "last_name": role.title(),
        "role": role,
    }
    if department_id is not None:
        payload["department_id"] = department_id
    response = client.post("/api/admin/invitations", json=payload, headers=auth_headers)
    assert response.status_code == 201, response.text
    body = response.json()
    return body, _extract_token(body["invitation_url"])


def _cleanup(db_engine, *, email: str, auth_user_ids: list[uuid.UUID]) -> None:
    with db_engine.connect() as conn:
        # USER_INVITED and INVITATION_ACCEPTED audit rows both reference the
        # invitation as resource_id - INVITATION_ACCEPTED's metadata has no
        # email field, so matching on resource_id (not metadata->>'email')
        # is what catches both.
        conn.execute(
            text(
                "DELETE FROM audit_logs WHERE resource_type = 'employee_invitations' "
                "AND resource_id IN (SELECT id FROM employee_invitations WHERE lower(email) = lower(:email))"
            ),
            {"email": email},
        )
        # employee_invitations.accepted_user_id is ON DELETE RESTRICT, so the
        # invitation row must go before the user row it references.
        conn.execute(text("DELETE FROM employee_invitations WHERE lower(email) = lower(:email)"), {"email": email})
        for auth_user_id in auth_user_ids:
            conn.execute(
                text(
                    "DELETE FROM doctors WHERE user_id IN (SELECT id FROM users WHERE auth_user_id = :a)"
                ),
                {"a": str(auth_user_id)},
            )
            conn.execute(
                text("DELETE FROM staff WHERE user_id IN (SELECT id FROM users WHERE auth_user_id = :a)"),
                {"a": str(auth_user_id)},
            )
            conn.execute(text("DELETE FROM users WHERE auth_user_id = :a"), {"a": str(auth_user_id)})
        conn.commit()


# --- provisioning: doctor -------------------------------------------------


def test_accept_provisions_doctor_with_department(db_engine, auth_as, auth_headers):
    with db_engine.connect() as conn:
        cardiology_id = str(conn.execute(text("SELECT id FROM departments WHERE code = 'CARD'")).scalar_one())

    invitation, token = _create_invitation(auth_as, auth_headers, role="DOCTOR", department_id=cardiology_id)
    email = invitation["email"]
    new_auth_id = str(uuid.uuid4())
    app.dependency_overrides[get_supabase_admin_client] = lambda: _FakeAdminClient(
        {new_auth_id: SupabaseUser(id=new_auth_id, email=email, email_confirmed_at=None)}
    )

    try:
        response = client.post(f"/api/invitations/{token}/accept", json={"auth_user_id": new_auth_id})
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["role"] == "DOCTOR"
        assert body["department_id"] == cardiology_id

        with db_engine.connect() as conn:
            user_id = conn.execute(
                text("SELECT id FROM users WHERE auth_user_id = :a"), {"a": new_auth_id}
            ).scalar_one()
            doctor_count = conn.execute(
                text("SELECT count(*) FROM doctors WHERE user_id = :u"), {"u": user_id}
            ).scalar_one()
            staff_count = conn.execute(
                text("SELECT count(*) FROM staff WHERE user_id = :u"), {"u": user_id}
            ).scalar_one()
        assert doctor_count == 1
        assert staff_count == 0
    finally:
        _cleanup(db_engine, email=email, auth_user_ids=[new_auth_id])


# --- provisioning: nurse/receptionist/staff -----------------------------


@pytest.mark.parametrize("role", ["NURSE", "RECEPTIONIST", "STAFF"])
def test_accept_provisions_staff_profile(db_engine, auth_as, auth_headers, role):
    invitation, token = _create_invitation(auth_as, auth_headers, role=role)
    email = invitation["email"]
    new_auth_id = str(uuid.uuid4())
    app.dependency_overrides[get_supabase_admin_client] = lambda: _FakeAdminClient(
        {new_auth_id: SupabaseUser(id=new_auth_id, email=email, email_confirmed_at=None)}
    )

    try:
        response = client.post(f"/api/invitations/{token}/accept", json={"auth_user_id": new_auth_id})
        assert response.status_code == 201, response.text
        assert response.json()["role"] == role

        with db_engine.connect() as conn:
            user_id = conn.execute(
                text("SELECT id FROM users WHERE auth_user_id = :a"), {"a": new_auth_id}
            ).scalar_one()
            designation = conn.execute(
                text("SELECT designation FROM staff WHERE user_id = :u"), {"u": user_id}
            ).scalar_one()
            doctor_count = conn.execute(
                text("SELECT count(*) FROM doctors WHERE user_id = :u"), {"u": user_id}
            ).scalar_one()
            patient_count = conn.execute(
                text("SELECT count(*) FROM patients WHERE user_id = :u"), {"u": user_id}
            ).scalar_one()
        assert designation == role
        assert doctor_count == 0
        assert patient_count == 0  # employees never get a patient profile
    finally:
        _cleanup(db_engine, email=email, auth_user_ids=[new_auth_id])


# --- token security ------------------------------------------------------


def test_accept_when_supabase_admin_api_unavailable_returns_503(db_engine, auth_as, auth_headers):
    """Failure recovery: if the Supabase Admin API can't be reached, the
    invitation stays PENDING (nothing partially provisioned) and the
    caller gets a clean 503, not a raw 500 or a half-created user."""
    invitation, token = _create_invitation(auth_as, auth_headers, role="STAFF")

    class _BrokenAdminClient:
        def get_user_by_id(self, auth_user_id: str) -> SupabaseUser | None:
            raise SupabaseAdminError("simulated Supabase outage")

    app.dependency_overrides[get_supabase_admin_client] = lambda: _BrokenAdminClient()
    try:
        response = client.post(
            f"/api/invitations/{token}/accept", json={"auth_user_id": str(uuid.uuid4())}
        )
        assert response.status_code == 503

        with db_engine.connect() as conn:
            status = conn.execute(
                text("SELECT status FROM employee_invitations WHERE id = :id"), {"id": invitation["id"]}
            ).scalar_one()
        assert status == "PENDING"
    finally:
        _cleanup(db_engine, email=invitation["email"], auth_user_ids=[])


def test_accept_invalid_token_denied():
    response = client.post(
        "/api/invitations/not-a-real-token/accept", json={"auth_user_id": str(uuid.uuid4())}
    )
    assert response.status_code == 404


def test_preview_invalid_token_404():
    response = client.get("/api/invitations/not-a-real-token")
    assert response.status_code == 404


def test_accept_expired_invitation_denied(db_engine, auth_as, auth_headers):
    invitation, token = _create_invitation(auth_as, auth_headers, role="STAFF")
    email = invitation["email"]
    with db_engine.connect() as conn:
        conn.execute(
            text("UPDATE employee_invitations SET expires_at = now() - interval '1 minute' WHERE id = :id"),
            {"id": invitation["id"]},
        )
        conn.commit()

    new_auth_id = str(uuid.uuid4())
    app.dependency_overrides[get_supabase_admin_client] = lambda: _FakeAdminClient(
        {new_auth_id: SupabaseUser(id=new_auth_id, email=email, email_confirmed_at=None)}
    )

    try:
        response = client.post(f"/api/invitations/{token}/accept", json={"auth_user_id": new_auth_id})
        assert response.status_code == 409

        with db_engine.connect() as conn:
            status = conn.execute(
                text("SELECT status FROM employee_invitations WHERE id = :id"), {"id": invitation["id"]}
            ).scalar_one()
        assert status == "EXPIRED"
    finally:
        _cleanup(db_engine, email=email, auth_user_ids=[new_auth_id])


def test_accept_revoked_invitation_denied(db_engine, auth_as, auth_headers):
    invitation, token = _create_invitation(auth_as, auth_headers, role="STAFF")
    email = invitation["email"]

    auth_as(HOSPITAL_ADMIN_EMAIL)
    revoke_response = client.post(f"/api/admin/invitations/{invitation['id']}/revoke", headers=auth_headers)
    assert revoke_response.status_code == 200
    assert revoke_response.json()["status"] == "REVOKED"

    new_auth_id = str(uuid.uuid4())
    app.dependency_overrides[get_supabase_admin_client] = lambda: _FakeAdminClient(
        {new_auth_id: SupabaseUser(id=new_auth_id, email=email, email_confirmed_at=None)}
    )
    try:
        response = client.post(f"/api/invitations/{token}/accept", json={"auth_user_id": new_auth_id})
        assert response.status_code == 409
    finally:
        _cleanup(db_engine, email=email, auth_user_ids=[new_auth_id])


def test_accept_email_mismatch_denied(db_engine, auth_as, auth_headers):
    invitation, token = _create_invitation(auth_as, auth_headers, role="STAFF")
    new_auth_id = str(uuid.uuid4())
    app.dependency_overrides[get_supabase_admin_client] = lambda: _FakeAdminClient(
        {new_auth_id: SupabaseUser(id=new_auth_id, email="someone-else@example.com", email_confirmed_at=None)}
    )
    try:
        response = client.post(f"/api/invitations/{token}/accept", json={"auth_user_id": new_auth_id})
        assert response.status_code == 400
    finally:
        _cleanup(db_engine, email=invitation["email"], auth_user_ids=[new_auth_id])


def test_accept_with_already_provisioned_identity_denied(db_engine, auth_as, auth_headers):
    invitation, token = _create_invitation(auth_as, auth_headers, role="STAFF")
    with db_engine.connect() as conn:
        existing_auth_id = str(
            conn.execute(
                text("SELECT auth_user_id FROM users WHERE email = 'rohan.mehta@caresphere-demo.example'")
            ).scalar_one()
        )
    app.dependency_overrides[get_supabase_admin_client] = lambda: _FakeAdminClient(
        {existing_auth_id: SupabaseUser(id=existing_auth_id, email=invitation["email"], email_confirmed_at=None)}
    )
    try:
        response = client.post(f"/api/invitations/{token}/accept", json={"auth_user_id": existing_auth_id})
        assert response.status_code == 409
    finally:
        _cleanup(db_engine, email=invitation["email"], auth_user_ids=[])


# --- replay protection ----------------------------------------------------


def test_accepted_invitation_cannot_be_accepted_again(db_engine, auth_as, auth_headers):
    invitation, token = _create_invitation(auth_as, auth_headers, role="STAFF")
    email = invitation["email"]
    first_auth_id = str(uuid.uuid4())
    second_auth_id = str(uuid.uuid4())
    app.dependency_overrides[get_supabase_admin_client] = lambda: _FakeAdminClient(
        {
            first_auth_id: SupabaseUser(id=first_auth_id, email=email, email_confirmed_at=None),
            second_auth_id: SupabaseUser(id=second_auth_id, email=email, email_confirmed_at=None),
        }
    )
    try:
        first = client.post(f"/api/invitations/{token}/accept", json={"auth_user_id": first_auth_id})
        assert first.status_code == 201

        replay = client.post(f"/api/invitations/{token}/accept", json={"auth_user_id": second_auth_id})
        assert replay.status_code == 409

        with db_engine.connect() as conn:
            user_count = conn.execute(
                text("SELECT count(*) FROM users WHERE auth_user_id IN (:a, :b)"),
                {"a": first_auth_id, "b": second_auth_id},
            ).scalar_one()
        assert user_count == 1  # only the first acceptance provisioned a user
    finally:
        _cleanup(db_engine, email=email, auth_user_ids=[first_auth_id, second_auth_id])


# --- concurrency -----------------------------------------------------------


def test_concurrent_acceptance_exactly_one_succeeds(db_engine, auth_as, auth_headers):
    invitation, token = _create_invitation(auth_as, auth_headers, role="DOCTOR")
    email = invitation["email"]
    auth_id_a = str(uuid.uuid4())
    auth_id_b = str(uuid.uuid4())
    app.dependency_overrides[get_supabase_admin_client] = lambda: _FakeAdminClient(
        {
            auth_id_a: SupabaseUser(id=auth_id_a, email=email, email_confirmed_at=None),
            auth_id_b: SupabaseUser(id=auth_id_b, email=email, email_confirmed_at=None),
        }
    )

    def _accept(auth_id: str):
        return client.post(f"/api/invitations/{token}/accept", json={"auth_user_id": auth_id})

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            future_a = pool.submit(_accept, auth_id_a)
            future_b = pool.submit(_accept, auth_id_b)
            status_codes = sorted([future_a.result().status_code, future_b.result().status_code])

        assert status_codes == [201, 409]

        with db_engine.connect() as conn:
            accepted_count = conn.execute(
                text(
                    "SELECT count(*) FROM employee_invitations WHERE id = :id AND status = 'ACCEPTED'"
                ),
                {"id": invitation["id"]},
            ).scalar_one()
            provisioned_count = conn.execute(
                text("SELECT count(*) FROM users WHERE auth_user_id IN (:a, :b)"),
                {"a": auth_id_a, "b": auth_id_b},
            ).scalar_one()
        assert accepted_count == 1
        assert provisioned_count == 1
    finally:
        _cleanup(db_engine, email=email, auth_user_ids=[auth_id_a, auth_id_b])


# --- preview ---------------------------------------------------------------


def test_preview_masks_email_and_shows_safe_fields(db_engine, auth_as, auth_headers):
    with db_engine.connect() as conn:
        cardiology_id = str(conn.execute(text("SELECT id FROM departments WHERE code = 'CARD'")).scalar_one())
    invitation, token = _create_invitation(auth_as, auth_headers, role="DOCTOR", department_id=cardiology_id)
    try:
        response = client.get(f"/api/invitations/{token}")
        assert response.status_code == 200
        body = response.json()
        assert body["email"] != invitation["email"]
        assert body["email"].endswith("@example.com")
        assert "*" in body["email"]
        assert body["role"] == "DOCTOR"
        assert body["department"] == "Cardiology"
        assert body["status"] == "PENDING"
        assert "invitation_token_hash" not in body
        assert "invited_by_user_id" not in body
    finally:
        _cleanup(db_engine, email=invitation["email"], auth_user_ids=[])
