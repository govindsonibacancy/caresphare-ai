import datetime
import uuid
from typing import Literal

from pydantic import BaseModel

# The EXISTING enum from database/migrations/0012_documents.sql
# (documents_document_type_check) - not the differently-worded list this
# phase's own brief suggested. See docs/RAG_INGESTION.md, "Existing
# document schema" for why the existing one is authoritative here.
DocumentType = Literal[
    "HOSPITAL_POLICY",
    "CLINICAL_GUIDELINE",
    "NURSING_PROCEDURE",
    "MEDICATION_GUIDELINE",
    "EMERGENCY_PROCEDURE",
    "PATIENT_EDUCATION",
    "HR_POLICY",
    "SOP",
    "GENERAL_INFORMATION",
]

# Four of the schema's five existing sensitivity values - PATIENT_SPECIFIC
# is deliberately not offered by this admin bulk-upload form; it requires a
# patient_id and belongs to a different workflow (a document about one
# named patient) than the hospital-knowledge-base documents this UI is for.
# See docs/RAG_INGESTION.md, "Access-control metadata".
DocumentSensitivity = Literal["PUBLIC", "INTERNAL", "CLINICAL", "CONFIDENTIAL"]

# Roles selectable in document_allowed_roles for this upload flow - every
# non-PATIENT role. PATIENT is excluded per this phase's own instructions
# ("do not allow PATIENT unless the existing product requirements
# explicitly support patient-facing knowledge documents" - they don't yet).
AllowedRoleName = Literal["DOCTOR", "NURSE", "RECEPTIONIST", "STAFF", "HOSPITAL_ADMIN", "SUPER_ADMIN"]


class DocumentSummary(BaseModel):
    """GET /api/admin/documents list item - metadata only, never raw chunk
    content or embeddings."""

    id: uuid.UUID
    title: str
    document_type: str
    department_id: uuid.UUID | None
    sensitivity: str
    status: str
    is_active: bool
    chunk_count: int
    uploaded_by: uuid.UUID
    created_at: datetime.datetime
    updated_at: datetime.datetime


class DocumentDetail(DocumentSummary):
    """GET /api/admin/documents/{id} - adds the fields not needed in a list
    row: description, file metadata, the failure message (if any), and the
    resolved access-control metadata (role names / doctor ids / staff ids -
    never raw chunk content)."""

    description: str | None
    filename: str
    mime_type: str | None
    file_size: int | None
    processing_error: str | None
    allowed_roles: list[str]
    authorized_doctor_ids: list[uuid.UUID]
    authorized_staff_ids: list[uuid.UUID]


class DocumentUpdateRequest(BaseModel):
    """PATCH /api/admin/documents/{id} - metadata only; replacing the file
    itself is a new upload (see docs/RAG_INGESTION.md, "Re-ingestion"), not
    an update to this one. Every field is optional; only supplied fields
    are changed."""

    title: str | None = None
    description: str | None = None
    department_id: uuid.UUID | None = None
    allowed_roles: list[AllowedRoleName] | None = None
    authorized_doctor_ids: list[uuid.UUID] | None = None
    authorized_staff_ids: list[uuid.UUID] | None = None
