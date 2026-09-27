"""Data access for `documents`/`document_chunks`/`document_allowed_roles`/
`document_authorized_doctors`/`document_authorized_staff` - all four tables
Phase 2 already created (database/migrations/0012-0013) plus the ingestion
metadata columns Phase 7 added (0017). No new tables here.
"""

import datetime
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

_DOCUMENT_COLUMNS = (
    "id, hospital_id, department_id, title, filename, document_type, description, "
    "sensitivity, patient_id, uploaded_by, status, mime_type, file_size, content_hash, "
    "processing_error, is_active, created_at, updated_at"
)


@dataclass(frozen=True)
class DocumentRecord:
    id: uuid.UUID
    hospital_id: uuid.UUID
    department_id: uuid.UUID | None
    title: str
    filename: str
    document_type: str
    description: str | None
    sensitivity: str
    patient_id: uuid.UUID | None
    uploaded_by: uuid.UUID
    status: str
    mime_type: str | None
    file_size: int | None
    content_hash: str | None
    processing_error: str | None
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


def get_document(db: Session, document_id: uuid.UUID) -> DocumentRecord | None:
    row = (
        db.execute(text(f"SELECT {_DOCUMENT_COLUMNS} FROM documents WHERE id = :id"), {"id": document_id})
        .mappings()
        .first()
    )
    return DocumentRecord(**row) if row else None


def find_active_document_by_hash(db: Session, hospital_id: uuid.UUID, content_hash: str) -> DocumentRecord | None:
    row = (
        db.execute(
            text(
                f"SELECT {_DOCUMENT_COLUMNS} FROM documents "
                "WHERE hospital_id = :hospital_id AND content_hash = :content_hash AND is_active"
            ),
            {"hospital_id": hospital_id, "content_hash": content_hash},
        )
        .mappings()
        .first()
    )
    return DocumentRecord(**row) if row else None


def create_document(
    db: Session,
    *,
    hospital_id: uuid.UUID,
    department_id: uuid.UUID | None,
    title: str,
    filename: str,
    document_type: str,
    description: str | None,
    sensitivity: str,
    uploaded_by: uuid.UUID,
    mime_type: str,
    file_size: int,
    content_hash: str,
) -> DocumentRecord:
    document_id = db.execute(
        text(
            """
            INSERT INTO documents (
                hospital_id, department_id, title, filename, document_type, description,
                sensitivity, uploaded_by, mime_type, file_size, content_hash, status
            )
            VALUES (
                :hospital_id, :department_id, :title, :filename, :document_type, :description,
                :sensitivity, :uploaded_by, :mime_type, :file_size, :content_hash, 'PENDING'
            )
            RETURNING id
            """
        ),
        {
            "hospital_id": hospital_id,
            "department_id": department_id,
            "title": title,
            "filename": filename,
            "document_type": document_type,
            "description": description,
            "sensitivity": sensitivity,
            "uploaded_by": uploaded_by,
            "mime_type": mime_type,
            "file_size": file_size,
            "content_hash": content_hash,
        },
    ).scalar_one()
    record = get_document(db, document_id)
    assert record is not None
    return record


def mark_processing(db: Session, document_id: uuid.UUID) -> None:
    db.execute(text("UPDATE documents SET status = 'PROCESSING' WHERE id = :id"), {"id": document_id})


def mark_completed(db: Session, document_id: uuid.UUID) -> None:
    db.execute(
        text("UPDATE documents SET status = 'COMPLETED', processing_error = NULL WHERE id = :id"),
        {"id": document_id},
    )


def mark_failed(db: Session, document_id: uuid.UUID, error: str) -> None:
    db.execute(
        text("UPDATE documents SET status = 'FAILED', processing_error = :error WHERE id = :id"),
        {"id": document_id, "error": error},
    )


def archive_document(db: Session, document_id: uuid.UUID) -> None:
    db.execute(text("UPDATE documents SET is_active = false WHERE id = :id"), {"id": document_id})


def update_document_metadata(
    db: Session,
    document_id: uuid.UUID,
    *,
    title: str | None = None,
    description: str | None | object = ...,
    department_id: uuid.UUID | None | object = ...,
) -> None:
    """Only columns whose keyword was actually passed are updated -
    `description`/`department_id` use a sentinel default (`...`) rather than
    `None`, since `None` is itself a valid value to set (clear the
    description, make a document hospital-wide)."""
    sets: list[str] = []
    params: dict[str, object] = {"id": document_id}
    if title is not None:
        sets.append("title = :title")
        params["title"] = title
    if description is not ...:
        sets.append("description = :description")
        params["description"] = description
    if department_id is not ...:
        sets.append("department_id = :department_id")
        params["department_id"] = department_id
    if not sets:
        return
    db.execute(text(f"UPDATE documents SET {', '.join(sets)} WHERE id = :id"), params)


def list_documents(
    db: Session,
    *,
    hospital_id: uuid.UUID,
    status: str | None = None,
    document_type: str | None = None,
    department_id: uuid.UUID | None = None,
    is_active: bool | None = None,
    limit: int,
    offset: int,
) -> tuple[list[DocumentRecord], int]:
    clauses = ["hospital_id = :hospital_id"]
    params: dict[str, object] = {"hospital_id": hospital_id}
    if status is not None:
        clauses.append("status = :status")
        params["status"] = status
    if document_type is not None:
        clauses.append("document_type = :document_type")
        params["document_type"] = document_type
    if department_id is not None:
        clauses.append("department_id = :department_id")
        params["department_id"] = department_id
    if is_active is not None:
        clauses.append("is_active = :is_active")
        params["is_active"] = is_active
    where = " AND ".join(clauses)

    total = db.execute(text(f"SELECT count(*) FROM documents WHERE {where}"), params).scalar_one()
    rows = (
        db.execute(
            text(f"SELECT {_DOCUMENT_COLUMNS} FROM documents WHERE {where} ORDER BY created_at DESC LIMIT :limit OFFSET :offset"),
            {**params, "limit": limit, "offset": offset},
        )
        .mappings()
        .all()
    )
    return [DocumentRecord(**row) for row in rows], total


# --- access-control metadata (document_allowed_roles / _authorized_doctors / _authorized_staff) --


def set_allowed_roles(db: Session, document_id: uuid.UUID, role_ids: list[uuid.UUID]) -> None:
    db.execute(text("DELETE FROM document_allowed_roles WHERE document_id = :id"), {"id": document_id})
    for role_id in role_ids:
        db.execute(
            text("INSERT INTO document_allowed_roles (document_id, role_id) VALUES (:document_id, :role_id)"),
            {"document_id": document_id, "role_id": role_id},
        )


def set_authorized_doctors(db: Session, document_id: uuid.UUID, doctor_ids: list[uuid.UUID]) -> None:
    db.execute(text("DELETE FROM document_authorized_doctors WHERE document_id = :id"), {"id": document_id})
    for doctor_id in doctor_ids:
        db.execute(
            text(
                "INSERT INTO document_authorized_doctors (document_id, doctor_id) VALUES (:document_id, :doctor_id)"
            ),
            {"document_id": document_id, "doctor_id": doctor_id},
        )


def set_authorized_staff(db: Session, document_id: uuid.UUID, staff_ids: list[uuid.UUID]) -> None:
    db.execute(text("DELETE FROM document_authorized_staff WHERE document_id = :id"), {"id": document_id})
    for staff_id in staff_ids:
        db.execute(
            text("INSERT INTO document_authorized_staff (document_id, staff_id) VALUES (:document_id, :staff_id)"),
            {"document_id": document_id, "staff_id": staff_id},
        )


def get_allowed_role_names(db: Session, document_id: uuid.UUID) -> list[str]:
    rows = db.execute(
        text(
            "SELECT r.name FROM document_allowed_roles dar JOIN roles r ON r.id = dar.role_id "
            "WHERE dar.document_id = :id ORDER BY r.name"
        ),
        {"id": document_id},
    ).scalars()
    return list(rows)


def get_authorized_doctor_ids(db: Session, document_id: uuid.UUID) -> list[uuid.UUID]:
    rows = db.execute(
        text("SELECT doctor_id FROM document_authorized_doctors WHERE document_id = :id ORDER BY doctor_id"),
        {"id": document_id},
    ).scalars()
    return list(rows)


def get_authorized_staff_ids(db: Session, document_id: uuid.UUID) -> list[uuid.UUID]:
    rows = db.execute(
        text("SELECT staff_id FROM document_authorized_staff WHERE document_id = :id ORDER BY staff_id"),
        {"id": document_id},
    ).scalars()
    return list(rows)


# --- chunks -----------------------------------------------------------


def get_chunk_count(db: Session, document_id: uuid.UUID) -> int:
    return db.execute(
        text("SELECT count(*) FROM document_chunks WHERE document_id = :id"), {"id": document_id}
    ).scalar_one()


def insert_chunk(
    db: Session,
    *,
    document_id: uuid.UUID,
    chunk_index: int,
    content: str,
    page_number: int | None,
    metadata: dict,
    embedding: list[float],
) -> None:
    # pgvector's text input format is a plain `[v1,v2,...]` literal - no
    # separate Python pgvector adapter package is needed to write one.
    embedding_literal = "[" + ",".join(repr(float(value)) for value in embedding) + "]"
    db.execute(
        text(
            """
            INSERT INTO document_chunks (document_id, chunk_index, content, page_number, metadata, embedding)
            VALUES (:document_id, :chunk_index, :content, :page_number, CAST(:metadata AS jsonb), CAST(:embedding AS vector))
            """
        ),
        {
            "document_id": document_id,
            "chunk_index": chunk_index,
            "content": content,
            "page_number": page_number,
            "metadata": json.dumps(metadata),
            "embedding": embedding_literal,
        },
    )


def delete_chunks(db: Session, document_id: uuid.UUID) -> None:
    db.execute(text("DELETE FROM document_chunks WHERE document_id = :id"), {"id": document_id})
