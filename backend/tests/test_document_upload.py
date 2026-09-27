"""POST /api/admin/documents: authentication, authorization (role/hospital/
department/doctor/staff escalation), file validation, metadata security,
and idempotency (duplicate-content detection).
"""

import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth.jwt_verifier import TokenClaims, TokenVerificationError, get_token_verifier
from app.main import app

client = TestClient(app)

HOSPITAL_ADMIN_EMAIL = "vikram.singh@caresphere-demo.example"
SUPER_ADMIN_EMAIL = "meera.kapoor@caresphere-demo.example"
STAFF_EMAIL = "neha.joshi@caresphere-demo.example"  # has manage_hospital_documents in the seed
DOCTOR_EMAIL = "rohan.mehta@caresphere-demo.example"  # has no manage_hospital_documents
PATIENT_EMAIL = "asha.verma@example-patient.example"


def _upload(headers, *, filename="policy.txt", content=b"Some policy content.", **form):
    files = {"file": (filename, content, "text/plain")}
    data = {
        "title": form.pop("title", "Test Document"),
        "document_type": form.pop("document_type", "HOSPITAL_POLICY"),
        "sensitivity": form.pop("sensitivity", "PUBLIC"),
        **form,
    }
    return client.post("/api/admin/documents", files=files, data=data, headers=headers)


# --- authentication --------------------------------------------------------


def test_upload_without_token_is_401():
    response = _upload({})
    assert response.status_code == 401


def test_upload_with_invalid_token_is_401():
    @dataclass
    class _FakeVerifier:
        def verify(self, token: str) -> TokenClaims:
            raise TokenVerificationError("bad signature")

    app.dependency_overrides[get_token_verifier] = lambda: _FakeVerifier()
    try:
        response = _upload({"Authorization": "Bearer bad"})
        assert response.status_code == 401
    finally:
        app.dependency_overrides.pop(get_token_verifier, None)


def test_upload_with_inactive_user_is_403(db_engine, auth_as, auth_headers):
    with db_engine.connect() as conn:
        row = conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": STAFF_EMAIL}).one()
        conn.execute(text("UPDATE users SET is_active = false WHERE id = :id"), {"id": row.id})
        conn.commit()
    try:
        auth_as(STAFF_EMAIL)
        response = _upload(auth_headers)
        assert response.status_code == 403
    finally:
        with db_engine.connect() as conn:
            conn.execute(text("UPDATE users SET is_active = true WHERE id = :id"), {"id": row.id})
            conn.commit()


# --- authorization: who can upload ----------------------------------------


@pytest.mark.parametrize("email", [HOSPITAL_ADMIN_EMAIL, SUPER_ADMIN_EMAIL, STAFF_EMAIL])
def test_roles_with_manage_hospital_documents_can_upload(auth_as, auth_headers, cleanup_document, email):
    """manage_hospital_documents is held by HOSPITAL_ADMIN, SUPER_ADMIN, AND
    STAFF in the seed - the existing permission, not a hardcoded role
    check, decides this. See docs/RAG_INGESTION.md, "Who can upload"."""
    auth_as(email)
    response = _upload(auth_headers, title=f"Doc by {email}")
    assert response.status_code == 201, response.text
    cleanup_document(response.json()["id"])


def test_doctor_without_manage_hospital_documents_denied(auth_as, auth_headers):
    auth_as(DOCTOR_EMAIL)
    response = _upload(auth_headers)
    assert response.status_code == 403


def test_patient_denied(auth_as, auth_headers):
    auth_as(PATIENT_EMAIL)
    response = _upload(auth_headers)
    assert response.status_code == 403


# --- authorization: hospital scope -----------------------------------------


def test_hospital_admin_cannot_upload_into_another_hospital(auth_as, auth_headers, second_hospital):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = _upload(auth_headers, hospital_id=str(second_hospital["hospital_id"]))
    assert response.status_code == 403


def test_super_admin_can_upload_into_another_hospital(db_engine, auth_as, auth_headers, cleanup_document, second_hospital):
    auth_as(SUPER_ADMIN_EMAIL)
    response = _upload(auth_headers, title="Cross-hospital doc", hospital_id=str(second_hospital["hospital_id"]))
    assert response.status_code == 201
    body = response.json()
    with db_engine.connect() as conn:
        actual_hospital_id = conn.execute(
            text("SELECT hospital_id FROM documents WHERE id = :id"), {"id": body["id"]}
        ).scalar_one()
    assert str(actual_hospital_id) == str(second_hospital["hospital_id"])
    cleanup_document(body["id"])


# --- authorization: department/doctor/staff escalation --------------------


def test_hospital_admin_cannot_use_another_hospitals_department(auth_as, auth_headers, second_hospital):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = _upload(auth_headers, department_id=str(second_hospital["department_id"]))
    assert response.status_code == 403


def test_hospital_admin_cannot_authorize_another_hospitals_doctor(auth_as, auth_headers, second_hospital_clinical_data):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = _upload(auth_headers, authorized_doctor_ids=[str(second_hospital_clinical_data["doctor_id"])])
    assert response.status_code == 403


def test_hospital_admin_cannot_authorize_another_hospitals_staff(auth_as, auth_headers, second_hospital_clinical_data):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = _upload(auth_headers, authorized_staff_ids=[str(second_hospital_clinical_data["staff_id"])])
    assert response.status_code == 403


def test_hospital_admin_can_use_own_hospital_department_and_doctor(db_engine, auth_as, auth_headers, cleanup_document):
    with db_engine.connect() as conn:
        cardiology_id = str(conn.execute(text("SELECT id FROM departments WHERE code = 'CARD'")).scalar_one())
        doctor_a_id = str(conn.execute(text("SELECT id FROM doctors WHERE employee_number = 'CGH-D-0001'")).scalar_one())
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = _upload(
        auth_headers,
        title="Cardiology-specific doc",
        department_id=cardiology_id,
        authorized_doctor_ids=[doctor_a_id],
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["department_id"] == cardiology_id
    assert body["authorized_doctor_ids"] == [doctor_a_id]
    cleanup_document(body["id"])


# --- file security -------------------------------------------------------


def test_unsupported_extension_rejected(auth_as, auth_headers):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = _upload(auth_headers, filename="malware.exe", content=b"not really an exe")
    assert response.status_code == 422


def test_empty_file_rejected(auth_as, auth_headers):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = _upload(auth_headers, content=b"")
    assert response.status_code == 422


def test_oversized_file_rejected(auth_as, auth_headers):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    oversized = b"x" * (21 * 1024 * 1024)  # over the 20 MB default limit
    response = _upload(auth_headers, content=oversized)
    assert response.status_code == 422


def test_unsafe_filename_rejected(auth_as, auth_headers):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = _upload(auth_headers, filename="../../etc/passwd.txt")
    assert response.status_code == 422


def test_path_traversal_filename_rejected(auth_as, auth_headers):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = _upload(auth_headers, filename="..%2f..%2fetc%2fpasswd.txt")
    assert response.status_code == 422


def test_corrupt_pdf_rejected(auth_as, auth_headers):
    """Extension says .pdf, but the bytes don't have the PDF magic header -
    not just trusting the extension."""
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = _upload(auth_headers, filename="fake.pdf", content=b"this is not a pdf")
    assert response.status_code == 422


def test_uploaded_file_is_never_stored_under_client_filename(auth_as, auth_headers, cleanup_document):
    """The storage layer names the file after the document's own server-
    generated id, never the client-supplied filename - see
    app/services/documents/storage.py. (Traversal-shaped filenames are
    separately rejected outright - see test_unsafe_filename_rejected/
    test_path_traversal_filename_rejected; this test uses an ordinary,
    valid filename to confirm what a *legitimate* upload is stored as.)
    """
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = _upload(auth_headers, filename="ordinary-policy-name.txt")
    assert response.status_code == 201
    body = response.json()
    import os

    from app.core.config import get_settings

    expected_path = os.path.join(get_settings().document_storage_path, f"{body['id']}.txt")
    assert os.path.isfile(expected_path)
    cleanup_document(body["id"])


# --- metadata security: client cannot set authoritative values ------------


def test_upload_ignores_client_supplied_authoritative_fields(db_engine, auth_as, auth_headers, cleanup_document):
    """status/chunk_count/content_hash/uploaded_by have no corresponding
    form field at all - submitting them alongside valid fields must not
    error (FastAPI ignores unknown form fields) and must have zero effect."""
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = _upload(
        auth_headers,
        title="Escalation attempt",
        status="COMPLETED",
        chunk_count="999",
        content_hash="deadbeef",
        uploaded_by=str(uuid.uuid4()),
    )
    assert response.status_code == 201
    body = response.json()
    with db_engine.connect() as conn:
        actual_uploader = conn.execute(
            text("SELECT uploaded_by FROM documents WHERE id = :id"), {"id": body["id"]}
        ).scalar_one()
        admin_user_id = conn.execute(
            text("SELECT id FROM users WHERE email = :e"), {"e": HOSPITAL_ADMIN_EMAIL}
        ).scalar_one()
    assert str(actual_uploader) == str(admin_user_id)  # never the client-submitted uuid
    assert body["status"] == "COMPLETED"  # legitimately COMPLETED - because ingestion succeeded, not because it was submitted
    cleanup_document(body["id"])


# --- idempotency -----------------------------------------------------------


def test_duplicate_content_upload_rejected(auth_as, auth_headers, cleanup_document):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    content = b"Identical content for duplicate detection test."
    first = _upload(auth_headers, title="First upload", content=content)
    assert first.status_code == 201
    try:
        second = _upload(auth_headers, title="Second upload, same bytes", content=content)
        assert second.status_code == 409
    finally:
        cleanup_document(first.json()["id"])


def test_same_content_different_hospital_is_not_a_duplicate(auth_as, auth_headers, cleanup_document, second_hospital):
    """Idempotency is scoped per hospital (the partial unique index is on
    (hospital_id, content_hash)) - the same policy text uploaded to two
    different hospitals is not a duplicate."""
    content = b"Shared boilerplate content across two hospitals."
    auth_as(HOSPITAL_ADMIN_EMAIL)
    first = _upload(auth_headers, title="Hospital A copy", content=content)
    assert first.status_code == 201

    auth_as(SUPER_ADMIN_EMAIL)
    second = _upload(auth_headers, title="Hospital B copy", content=content, hospital_id=str(second_hospital["hospital_id"]))
    try:
        assert second.status_code == 201
    finally:
        cleanup_document(first.json()["id"])
        if second.status_code == 201:
            cleanup_document(second.json()["id"])


def test_reuploading_after_archive_is_allowed(auth_as, auth_headers, cleanup_document):
    """Archiving (is_active = false) removes a document from the partial
    unique index's scope, so the same content can be re-uploaded - see
    docs/RAG_INGESTION.md, "Re-ingestion"."""
    auth_as(HOSPITAL_ADMIN_EMAIL)
    content = b"Content that will be archived and re-uploaded."
    first = _upload(auth_headers, title="Original", content=content)
    assert first.status_code == 201
    first_id = first.json()["id"]

    archive_response = client.post(f"/api/admin/documents/{first_id}/archive", headers=auth_headers)
    assert archive_response.status_code == 200
    assert archive_response.json()["is_active"] is False

    try:
        second = _upload(auth_headers, title="Replacement", content=content)
        assert second.status_code == 201
        cleanup_document(second.json()["id"])
    finally:
        cleanup_document(first_id)
