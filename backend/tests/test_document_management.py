"""GET /api/admin/documents (list + detail), PATCH /api/admin/documents/{id},
POST /api/admin/documents/{id}/archive: hospital scoping, pagination/filters,
enumeration protection (404 for both nonexistent and cross-hospital), and
authorization-checked metadata updates.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

HOSPITAL_ADMIN_EMAIL = "vikram.singh@caresphere-demo.example"
SUPER_ADMIN_EMAIL = "meera.kapoor@caresphere-demo.example"
DOCTOR_EMAIL = "rohan.mehta@caresphere-demo.example"  # has no manage_hospital_documents
PATIENT_EMAIL = "asha.verma@example-patient.example"


def _upload(headers, *, filename="policy.txt", content=b"Some policy content.", **form):
    files = {"file": (filename, content, "text/plain")}
    data = {
        "title": form.pop("title", "Management Test Document"),
        "document_type": form.pop("document_type", "HOSPITAL_POLICY"),
        "sensitivity": form.pop("sensitivity", "PUBLIC"),
        **form,
    }
    return client.post("/api/admin/documents", files=files, data=data, headers=headers)


@pytest.fixture
def tracked_documents(cleanup_document):
    """Uploads made via `_uploaded()` register their id here instead of
    being cleaned up immediately, so the document still exists for the
    rest of the test - cleanup runs once at teardown."""
    ids: list[str] = []
    yield ids
    for document_id in ids:
        cleanup_document(document_id)


def _uploaded(auth_as, auth_headers, tracked_documents, **form):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = _upload(auth_headers, **form)
    assert response.status_code == 201, response.text
    body = response.json()
    tracked_documents.append(body["id"])
    return body


# --- authentication / authorization ----------------------------------------


def test_list_without_token_is_401():
    response = client.get("/api/admin/documents")
    assert response.status_code == 401


def test_list_denied_for_role_without_manage_hospital_documents(auth_as, auth_headers):
    auth_as(DOCTOR_EMAIL)
    response = client.get("/api/admin/documents", headers=auth_headers)
    assert response.status_code == 403


def test_list_denied_for_patient(auth_as, auth_headers):
    auth_as(PATIENT_EMAIL)
    response = client.get("/api/admin/documents", headers=auth_headers)
    assert response.status_code == 403


# --- list: hospital scoping and shape ---------------------------------------


def test_list_returns_page_shape_and_uploaded_document(auth_as, auth_headers, tracked_documents):
    doc = _uploaded(auth_as, auth_headers, tracked_documents, title="Listed Document")
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get("/api/admin/documents", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert {"items", "page", "page_size", "total"} <= body.keys()
    assert any(item["id"] == doc["id"] for item in body["items"])
    listed = next(item for item in body["items"] if item["id"] == doc["id"])
    # list rows are metadata only - never raw chunk content or embeddings
    assert "embedding" not in listed
    assert "chunks" not in listed
    assert "content" not in listed


def test_hospital_admin_only_sees_own_hospital_documents(auth_as, auth_headers, tracked_documents, second_hospital):
    doc = _uploaded(auth_as, auth_headers, tracked_documents, title="Hospital A only")
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get("/api/admin/documents", headers=auth_headers)
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert doc["id"] in ids


def test_hospital_admin_cannot_list_another_hospitals_documents_via_hospital_id(
    auth_as, auth_headers, second_hospital
):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get(
        "/api/admin/documents", params={"hospital_id": str(second_hospital["hospital_id"])}, headers=auth_headers
    )
    assert response.status_code == 403


def test_super_admin_can_list_another_hospitals_documents_via_hospital_id(
    auth_as, auth_headers, cleanup_document, second_hospital
):
    auth_as(SUPER_ADMIN_EMAIL)
    upload = _upload(
        auth_headers, title="Hospital B doc", hospital_id=str(second_hospital["hospital_id"])
    )
    assert upload.status_code == 201
    doc = upload.json()
    try:
        response = client.get(
            "/api/admin/documents", params={"hospital_id": str(second_hospital["hospital_id"])}, headers=auth_headers
        )
        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["items"]}
        assert doc["id"] in ids
    finally:
        cleanup_document(doc["id"])


# --- list: filters and pagination -------------------------------------------


def test_list_filters_by_document_type(auth_as, auth_headers, tracked_documents):
    policy = _uploaded(
        auth_as, auth_headers, tracked_documents, title="A policy doc", document_type="HOSPITAL_POLICY",
        content=b"Policy document content.",
    )
    guideline = _uploaded(
        auth_as, auth_headers, tracked_documents, title="A guideline doc", document_type="CLINICAL_GUIDELINE",
        content=b"Guideline document content.",
    )
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get(
        "/api/admin/documents", params={"document_type": "CLINICAL_GUIDELINE"}, headers=auth_headers
    )
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert guideline["id"] in ids
    assert policy["id"] not in ids


def test_list_filters_by_is_active(auth_as, auth_headers, tracked_documents):
    doc = _uploaded(auth_as, auth_headers, tracked_documents, title="Will be archived")
    auth_as(HOSPITAL_ADMIN_EMAIL)
    archive_response = client.post(f"/api/admin/documents/{doc['id']}/archive", headers=auth_headers)
    assert archive_response.status_code == 200

    active_only = client.get("/api/admin/documents", params={"is_active": True}, headers=auth_headers)
    assert doc["id"] not in {item["id"] for item in active_only.json()["items"]}

    archived_only = client.get("/api/admin/documents", params={"is_active": False}, headers=auth_headers)
    assert doc["id"] in {item["id"] for item in archived_only.json()["items"]}


def test_list_pagination_respects_page_size(auth_as, auth_headers, tracked_documents):
    for i in range(3):
        _uploaded(auth_as, auth_headers, tracked_documents, title=f"Paginated doc {i}", content=f"content {i}".encode())
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get("/api/admin/documents", params={"page": 1, "page_size": 2}, headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) <= 2
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert body["total"] >= 3


# --- detail: enumeration protection ------------------------------------------


def test_get_detail_returns_full_metadata(auth_as, auth_headers, tracked_documents):
    doc = _uploaded(auth_as, auth_headers, tracked_documents, title="Detail Document", description="A description")
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get(f"/api/admin/documents/{doc['id']}", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["description"] == "A description"
    assert body["filename"] == "policy.txt"
    assert "allowed_roles" in body
    assert "authorized_doctor_ids" in body
    assert "authorized_staff_ids" in body


def test_get_detail_nonexistent_document_is_404(auth_as, auth_headers):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.get(f"/api/admin/documents/{uuid.uuid4()}", headers=auth_headers)
    assert response.status_code == 404


def test_get_detail_cross_hospital_document_is_404_not_403(
    auth_as, auth_headers, cleanup_document, second_hospital
):
    """A document that exists but belongs to another hospital must look
    identical to one that doesn't exist at all - never a 403 that would
    confirm its existence to an unauthorized caller."""
    auth_as(SUPER_ADMIN_EMAIL)
    upload = _upload(auth_headers, title="Hospital B only", hospital_id=str(second_hospital["hospital_id"]))
    assert upload.status_code == 201
    doc = upload.json()
    try:
        auth_as(HOSPITAL_ADMIN_EMAIL)
        response = client.get(f"/api/admin/documents/{doc['id']}", headers=auth_headers)
        assert response.status_code == 404
    finally:
        cleanup_document(doc["id"])


def test_super_admin_can_view_any_hospitals_document_detail(auth_as, auth_headers, cleanup_document, second_hospital):
    auth_as(SUPER_ADMIN_EMAIL)
    upload = _upload(auth_headers, title="Hospital B detail", hospital_id=str(second_hospital["hospital_id"]))
    assert upload.status_code == 201
    doc = upload.json()
    try:
        response = client.get(f"/api/admin/documents/{doc['id']}", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["id"] == doc["id"]
    finally:
        cleanup_document(doc["id"])


# --- update -----------------------------------------------------------------


def test_patch_updates_only_supplied_fields(auth_as, auth_headers, tracked_documents):
    doc = _uploaded(auth_as, auth_headers, tracked_documents, title="Original Title", description="Original description")
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.patch(f"/api/admin/documents/{doc['id']}", json={"title": "Updated Title"}, headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Updated Title"
    assert body["description"] == "Original description"  # untouched, since it wasn't in the payload


def test_patch_can_explicitly_clear_description(auth_as, auth_headers, tracked_documents):
    doc = _uploaded(auth_as, auth_headers, tracked_documents, description="To be cleared")
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.patch(f"/api/admin/documents/{doc['id']}", json={"description": None}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["description"] is None


def test_patch_updates_allowed_roles(auth_as, auth_headers, tracked_documents):
    doc = _uploaded(auth_as, auth_headers, tracked_documents)
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.patch(
        f"/api/admin/documents/{doc['id']}", json={"allowed_roles": ["DOCTOR", "NURSE"]}, headers=auth_headers
    )
    assert response.status_code == 200
    assert set(response.json()["allowed_roles"]) == {"DOCTOR", "NURSE"}


def test_patch_nonexistent_document_is_404(auth_as, auth_headers):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.patch(f"/api/admin/documents/{uuid.uuid4()}", json={"title": "x"}, headers=auth_headers)
    assert response.status_code == 404


def test_patch_cross_hospital_document_is_404(auth_as, auth_headers, cleanup_document, second_hospital):
    auth_as(SUPER_ADMIN_EMAIL)
    upload = _upload(auth_headers, title="Hospital B patch target", hospital_id=str(second_hospital["hospital_id"]))
    assert upload.status_code == 201
    doc = upload.json()
    try:
        auth_as(HOSPITAL_ADMIN_EMAIL)
        response = client.patch(f"/api/admin/documents/{doc['id']}", json={"title": "hijacked"}, headers=auth_headers)
        assert response.status_code == 404
    finally:
        cleanup_document(doc["id"])


def test_patch_rejects_department_from_another_hospital(auth_as, auth_headers, tracked_documents, second_hospital):
    doc = _uploaded(auth_as, auth_headers, tracked_documents)
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.patch(
        f"/api/admin/documents/{doc['id']}",
        json={"department_id": str(second_hospital["department_id"])},
        headers=auth_headers,
    )
    assert response.status_code == 403


def test_patch_rejects_authorized_doctor_from_another_hospital(
    auth_as, auth_headers, tracked_documents, second_hospital_clinical_data
):
    doc = _uploaded(auth_as, auth_headers, tracked_documents)
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.patch(
        f"/api/admin/documents/{doc['id']}",
        json={"authorized_doctor_ids": [str(second_hospital_clinical_data["doctor_id"])]},
        headers=auth_headers,
    )
    assert response.status_code == 403


def test_patch_rejects_authorized_staff_from_another_hospital(
    auth_as, auth_headers, tracked_documents, second_hospital_clinical_data
):
    doc = _uploaded(auth_as, auth_headers, tracked_documents)
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.patch(
        f"/api/admin/documents/{doc['id']}",
        json={"authorized_staff_ids": [str(second_hospital_clinical_data["staff_id"])]},
        headers=auth_headers,
    )
    assert response.status_code == 403


# --- archive ------------------------------------------------------------


def test_archive_sets_is_active_false(auth_as, auth_headers, tracked_documents):
    doc = _uploaded(auth_as, auth_headers, tracked_documents)
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.post(f"/api/admin/documents/{doc['id']}/archive", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["is_active"] is False


def test_archive_nonexistent_document_is_404(auth_as, auth_headers):
    auth_as(HOSPITAL_ADMIN_EMAIL)
    response = client.post(f"/api/admin/documents/{uuid.uuid4()}/archive", headers=auth_headers)
    assert response.status_code == 404


def test_archive_cross_hospital_document_is_404(auth_as, auth_headers, cleanup_document, second_hospital):
    auth_as(SUPER_ADMIN_EMAIL)
    upload = _upload(auth_headers, title="Hospital B archive target", hospital_id=str(second_hospital["hospital_id"]))
    assert upload.status_code == 201
    doc = upload.json()
    try:
        auth_as(HOSPITAL_ADMIN_EMAIL)
        response = client.post(f"/api/admin/documents/{doc['id']}/archive", headers=auth_headers)
        assert response.status_code == 404
    finally:
        cleanup_document(doc["id"])


def test_archive_denied_for_role_without_manage_hospital_documents(auth_as, auth_headers, tracked_documents):
    doc = _uploaded(auth_as, auth_headers, tracked_documents)
    auth_as(DOCTOR_EMAIL)
    response = client.post(f"/api/admin/documents/{doc['id']}/archive", headers=auth_headers)
    assert response.status_code == 403
