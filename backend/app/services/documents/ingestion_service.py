"""Document upload + ingestion orchestration: validate -> store -> extract
-> clean -> chunk -> embed -> persist chunks -> COMPLETED (or FAILED at any
step). See docs/RAG_INGESTION.md for the full lifecycle and the
transactional-safety reasoning below.

Runs synchronously within the upload request - no background job queue
exists in this project yet (Ollama/task workers are later phases), and for
a local/demo system with small documents this is the simplest correct
choice. A later phase could move the extract/chunk/embed steps to a
background worker without changing this module's public functions much:
`create_document` and `run_ingestion` are already separate calls.
"""

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import events as audit_events
from app.core.config import get_settings
from app.permissions.authorization import resolve_hospital_scope
from app.permissions.exceptions import PermissionDenied
from app.permissions.roles import Role
from app.permissions.scope import UserScope
from app.repositories import clinical_repository, document_repository, invitation_repository
from app.repositories.document_repository import DocumentRecord
from app.schemas.documents import AllowedRoleName, DocumentSensitivity, DocumentType, DocumentUpdateRequest
from app.services.documents import validation
from app.services.documents.chunking import chunk_pages
from app.services.documents.cleaning import clean_text
from app.services.documents.embedding import EmbeddingDimensionMismatch, EmbeddingServiceError, get_embedding_service
from app.services.documents.extraction import ExtractionError, extract_text
from app.services.documents.storage import get_file_storage

INVITABLE_DOCUMENT_ROLES = frozenset(
    {Role.DOCTOR, Role.NURSE, Role.RECEPTIONIST, Role.STAFF, Role.HOSPITAL_ADMIN, Role.SUPER_ADMIN}
)


class DuplicateDocument(Exception):
    """An active document with identical content already exists in this
    hospital - see docs/RAG_INGESTION.md, "Idempotency"."""


class DocumentNotFound(Exception):
    pass


@dataclass(frozen=True)
class UploadMetadata:
    title: str
    document_type: DocumentType
    description: str | None
    sensitivity: DocumentSensitivity
    department_id: uuid.UUID | None
    hospital_id: uuid.UUID | None  # SUPER_ADMIN only - see resolve_hospital_scope
    allowed_roles: list[AllowedRoleName]
    authorized_doctor_ids: list[uuid.UUID]
    authorized_staff_ids: list[uuid.UUID]


def _validate_access_metadata(
    db: Session,
    *,
    hospital_id: uuid.UUID,
    department_id: uuid.UUID | None,
    authorized_doctor_ids: list[uuid.UUID],
    authorized_staff_ids: list[uuid.UUID],
) -> None:
    """Every id the client submitted for access scope is independently
    re-checked against the resolved target hospital - never trusted just
    because the client selected it in a form. See docs/RAG_INGESTION.md,
    "Authorization"."""
    if department_id is not None and not invitation_repository.department_belongs_to_hospital(
        db, department_id, hospital_id
    ):
        raise PermissionDenied("department does not belong to the target hospital")
    for doctor_id in authorized_doctor_ids:
        if clinical_repository.get_doctor_hospital_id(db, doctor_id) != hospital_id:
            raise PermissionDenied("an authorized doctor does not belong to the target hospital")
    for staff_id in authorized_staff_ids:
        if clinical_repository.get_staff_hospital_id(db, staff_id) != hospital_id:
            raise PermissionDenied("an authorized staff member does not belong to the target hospital")


def upload_document(
    db: Session,
    *,
    scope: UserScope,
    uploaded_by: uuid.UUID,
    filename: str,
    content: bytes,
    metadata: UploadMetadata,
) -> DocumentRecord:
    """Validates the upload, stores it, creates the `documents` row
    (PENDING), then immediately runs ingestion. Returns the final record
    (COMPLETED or FAILED - never PENDING/PROCESSING, since ingestion has
    already finished by the time this returns).
    """
    settings = get_settings()
    validated = validation.validate_upload(
        filename=filename, content=content, max_size_bytes=settings.max_document_upload_size_mb * 1024 * 1024
    )

    hospital_id = resolve_hospital_scope(scope, metadata.hospital_id)
    _validate_access_metadata(
        db,
        hospital_id=hospital_id,
        department_id=metadata.department_id,
        authorized_doctor_ids=metadata.authorized_doctor_ids,
        authorized_staff_ids=metadata.authorized_staff_ids,
    )

    content_hash = hashlib.sha256(content).hexdigest()
    if document_repository.find_active_document_by_hash(db, hospital_id, content_hash) is not None:
        raise DuplicateDocument("An active document with identical content already exists in this hospital.")

    try:
        with db.begin_nested():
            record = document_repository.create_document(
                db,
                hospital_id=hospital_id,
                department_id=metadata.department_id,
                title=metadata.title,
                filename=filename,
                document_type=metadata.document_type,
                description=metadata.description,
                sensitivity=metadata.sensitivity,
                uploaded_by=uploaded_by,
                mime_type=validated.mime_type,
                file_size=validated.size,
                content_hash=content_hash,
            )
    except IntegrityError as exc:
        # Closes the race the pre-check above can't: two uploads of the
        # same content arriving concurrently. The partial unique index
        # (documents_hospital_content_hash_active_key) is the real
        # guarantee; the find_active_document_by_hash check above is just
        # the fast, common-case path that avoids storing+extracting+
        # embedding a file we're about to reject anyway.
        raise DuplicateDocument("An active document with identical content already exists in this hospital.") from exc

    role_ids = [
        role_id
        for role_id in (invitation_repository.get_role_id_by_name(db, role) for role in metadata.allowed_roles)
        if role_id is not None
    ]
    document_repository.set_allowed_roles(db, record.id, role_ids)
    document_repository.set_authorized_doctors(db, record.id, metadata.authorized_doctor_ids)
    document_repository.set_authorized_staff(db, record.id, metadata.authorized_staff_ids)

    get_file_storage().save(record.id, validated.extension, content)

    audit_events.record_event(
        db,
        actor_user_id=uploaded_by,
        action="DOCUMENT_UPLOADED",
        resource_type="documents",
        resource_id=record.id,
        hospital_id=hospital_id,
        metadata={"document_type": metadata.document_type, "sensitivity": metadata.sensitivity},
    )
    db.commit()

    return _run_ingestion(db, document_id=record.id, extension=validated.extension, content=content)


def _run_ingestion(db: Session, *, document_id: uuid.UUID, extension: str, content: bytes) -> DocumentRecord:
    """A failure at any step here marks the document FAILED with a safe
    message and stops - it never leaves a document COMPLETED with missing
    or partial chunks (see docs/RAG_INGESTION.md, "Transactional safety").
    document_repository.delete_chunks is called before re-inserting so a
    retry (if this function is ever called again for the same document)
    can't accumulate duplicate chunks alongside old ones.
    """
    document_repository.mark_processing(db, document_id)
    db.commit()

    try:
        extracted = extract_text(extension, content)
        pages = [(clean_text(page.text), page.page_number) for page in extracted.pages]
        pages = [(text, page_number) for text, page_number in pages if text]
        if not pages:
            raise ExtractionError("No extractable text was found in this document.")

        settings = get_settings()
        chunks = chunk_pages(
            pages, chunk_size=settings.document_chunk_size_chars, overlap=settings.document_chunk_overlap_chars
        )
        if not chunks:
            raise ExtractionError("Text was extracted but produced no chunks.")

        embeddings = get_embedding_service().embed([chunk.content for chunk in chunks])

        document_repository.delete_chunks(db, document_id)
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            document_repository.insert_chunk(
                db,
                document_id=document_id,
                chunk_index=chunk.index,
                content=chunk.content,
                page_number=chunk.page_number,
                metadata={"heading": chunk.heading} if chunk.heading else {},
                embedding=embedding,
            )
        document_repository.mark_completed(db, document_id)
        db.commit()
    except (ExtractionError, EmbeddingServiceError, EmbeddingDimensionMismatch) as exc:
        _fail_ingestion(db, document_id=document_id, reason=str(exc))
    except Exception:
        # Anything else (a DB error mid-insert, etc.) - never leak the raw
        # exception message (could contain SQL text, a file path, ...) into
        # a column an administrator reads. Still never leaves the document
        # COMPLETED with partial/missing chunks.
        _fail_ingestion(db, document_id=document_id, reason="An unexpected error occurred while processing this document.")

    record = document_repository.get_document(db, document_id)
    assert record is not None
    return record


def _fail_ingestion(db: Session, *, document_id: uuid.UUID, reason: str) -> None:
    db.rollback()
    document_repository.mark_failed(db, document_id, reason)
    record = document_repository.get_document(db, document_id)
    audit_events.record_event(
        db,
        actor_user_id=None,
        action="DOCUMENT_PROCESSING_FAILED",
        resource_type="documents",
        resource_id=document_id,
        hospital_id=record.hospital_id if record else None,
        metadata={"reason": reason},
    )
    db.commit()


def _get_owned_document(db: Session, scope: UserScope, document_id: uuid.UUID) -> DocumentRecord:
    record = document_repository.get_document(db, document_id)
    if record is None:
        raise DocumentNotFound("document not found")
    if scope.role is not Role.SUPER_ADMIN and record.hospital_id != scope.hospital_id:
        # A document in another hospital and one that doesn't exist look
        # identical to the caller - see docs/RAG_INGESTION.md, "Enumeration
        # protection" (the same posture Phase 6 already established).
        raise DocumentNotFound("document not found")
    return record


def update_document(
    db: Session, *, scope: UserScope, document_id: uuid.UUID, payload: DocumentUpdateRequest
) -> DocumentRecord:
    """Only fields actually present in the request are changed
    (`payload.model_fields_set`) - `department_id`/`description` use this to
    distinguish "not provided, leave alone" from "explicitly set to null,
    clear it". Every doctor/staff/department id in the payload is
    independently re-validated against the document's own hospital, never
    trusted because the client selected it - identical posture to upload.
    """
    record = _get_owned_document(db, scope, document_id)
    fields_set = payload.model_fields_set

    if "department_id" in fields_set and payload.department_id is not None:
        if not invitation_repository.department_belongs_to_hospital(db, payload.department_id, record.hospital_id):
            raise PermissionDenied("department does not belong to the document's hospital")

    if "authorized_doctor_ids" in fields_set and payload.authorized_doctor_ids is not None:
        for doctor_id in payload.authorized_doctor_ids:
            if clinical_repository.get_doctor_hospital_id(db, doctor_id) != record.hospital_id:
                raise PermissionDenied("an authorized doctor does not belong to the document's hospital")

    if "authorized_staff_ids" in fields_set and payload.authorized_staff_ids is not None:
        for staff_id in payload.authorized_staff_ids:
            if clinical_repository.get_staff_hospital_id(db, staff_id) != record.hospital_id:
                raise PermissionDenied("an authorized staff member does not belong to the document's hospital")

    document_repository.update_document_metadata(
        db,
        document_id,
        title=payload.title,
        description=payload.description if "description" in fields_set else ...,
        department_id=payload.department_id if "department_id" in fields_set else ...,
    )
    if "allowed_roles" in fields_set and payload.allowed_roles is not None:
        role_ids = [
            role_id
            for role_id in (invitation_repository.get_role_id_by_name(db, role) for role in payload.allowed_roles)
            if role_id is not None
        ]
        document_repository.set_allowed_roles(db, document_id, role_ids)
    if "authorized_doctor_ids" in fields_set and payload.authorized_doctor_ids is not None:
        document_repository.set_authorized_doctors(db, document_id, payload.authorized_doctor_ids)
    if "authorized_staff_ids" in fields_set and payload.authorized_staff_ids is not None:
        document_repository.set_authorized_staff(db, document_id, payload.authorized_staff_ids)

    audit_events.record_event(
        db,
        actor_user_id=scope.user_id,
        action="DOCUMENT_UPDATED",
        resource_type="documents",
        resource_id=document_id,
        hospital_id=record.hospital_id,
    )
    db.commit()

    updated = document_repository.get_document(db, document_id)
    assert updated is not None
    return updated


def archive_document(db: Session, *, scope: UserScope, document_id: uuid.UUID, actor_user_id: uuid.UUID) -> DocumentRecord:
    record = _get_owned_document(db, scope, document_id)

    document_repository.archive_document(db, document_id)
    audit_events.record_event(
        db,
        actor_user_id=actor_user_id,
        action="DOCUMENT_ARCHIVED",
        resource_type="documents",
        resource_id=document_id,
        hospital_id=record.hospital_id,
    )
    db.commit()

    updated = document_repository.get_document(db, document_id)
    assert updated is not None
    return updated
