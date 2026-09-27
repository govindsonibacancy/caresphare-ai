import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.permissions.authorization import resolve_hospital_scope
from app.permissions.dependencies import require_permission
from app.permissions.exceptions import PermissionDenied
from app.permissions.permissions import Permission
from app.permissions.roles import Role
from app.permissions.scope import UserScope
from app.repositories import document_repository
from app.repositories.document_repository import DocumentRecord
from app.schemas.documents import (
    AllowedRoleName,
    DocumentDetail,
    DocumentSensitivity,
    DocumentSummary,
    DocumentType,
    DocumentUpdateRequest,
)
from app.schemas.pagination import Page
from app.services.documents import validation
from app.services.documents.ingestion_service import (
    DocumentNotFound,
    DuplicateDocument,
    UploadMetadata,
    archive_document,
    update_document,
    upload_document,
)

router = APIRouter(prefix="/api/admin/documents", tags=["admin-documents"])

GATE_PERMISSION = Permission.MANAGE_HOSPITAL_DOCUMENTS


def _to_summary(record: DocumentRecord, chunk_count: int) -> DocumentSummary:
    return DocumentSummary(
        id=record.id,
        title=record.title,
        document_type=record.document_type,
        department_id=record.department_id,
        sensitivity=record.sensitivity,
        status=record.status,
        is_active=record.is_active,
        chunk_count=chunk_count,
        uploaded_by=record.uploaded_by,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _to_detail(db: Session, record: DocumentRecord) -> DocumentDetail:
    chunk_count = document_repository.get_chunk_count(db, record.id)
    return DocumentDetail(
        **_to_summary(record, chunk_count).model_dump(),
        description=record.description,
        filename=record.filename,
        mime_type=record.mime_type,
        file_size=record.file_size,
        processing_error=record.processing_error,
        allowed_roles=document_repository.get_allowed_role_names(db, record.id),
        authorized_doctor_ids=document_repository.get_authorized_doctor_ids(db, record.id),
        authorized_staff_ids=document_repository.get_authorized_staff_ids(db, record.id),
    )


def _forbidden() -> HTTPException:
    return HTTPException(status_code=403, detail="You do not have permission to perform this action.")


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Document not found.")


@router.post("", response_model=DocumentDetail, status_code=201)
async def upload(
    file: UploadFile = File(...),
    title: str = Form(...),
    document_type: DocumentType = Form(...),
    sensitivity: DocumentSensitivity = Form(...),
    description: str | None = Form(default=None),
    department_id: uuid.UUID | None = Form(default=None),
    hospital_id: uuid.UUID | None = Form(default=None),
    allowed_roles: list[AllowedRoleName] = Form(default=[]),
    authorized_doctor_ids: list[uuid.UUID] = Form(default=[]),
    authorized_staff_ids: list[uuid.UUID] = Form(default=[]),
    scope: UserScope = Depends(require_permission(GATE_PERMISSION)),
    db: Session = Depends(get_db),
) -> DocumentDetail:
    """`hospital_id`/`department_id`/`allowed_roles`/`authorized_doctor_ids`/
    `authorized_staff_ids` are the only scope-related fields a client can
    submit, and every one is independently re-validated against the
    resolved target hospital server-side (see
    app/services/documents/ingestion_service.py) - none is trusted just
    because it was submitted. `uploaded_by`, the real `hospital_id` (for
    everyone but SUPER_ADMIN), `status`, `chunk_count`, and `content_hash`
    are never accepted from the client at all - there is no field for any
    of them here.
    """
    content = await file.read()
    metadata = UploadMetadata(
        title=title,
        document_type=document_type,
        description=description,
        sensitivity=sensitivity,
        department_id=department_id,
        hospital_id=hospital_id,
        allowed_roles=allowed_roles,
        authorized_doctor_ids=authorized_doctor_ids,
        authorized_staff_ids=authorized_staff_ids,
    )
    try:
        record = upload_document(
            db,
            scope=scope,
            uploaded_by=scope.user_id,
            filename=file.filename or "",
            content=content,
            metadata=metadata,
        )
    except PermissionDenied as exc:
        raise _forbidden() from exc
    except validation.DocumentValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DuplicateDocument as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _to_detail(db, record)


@router.get("", response_model=Page[DocumentSummary])
def list_documents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None),
    document_type: DocumentType | None = Query(default=None),
    department_id: uuid.UUID | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    hospital_id: uuid.UUID | None = Query(default=None),
    scope: UserScope = Depends(require_permission(GATE_PERMISSION)),
    db: Session = Depends(get_db),
) -> Page[DocumentSummary]:
    try:
        target_hospital_id = resolve_hospital_scope(scope, hospital_id)
    except PermissionDenied as exc:
        raise _forbidden() from exc

    records, total = document_repository.list_documents(
        db,
        hospital_id=target_hospital_id,
        status=status,
        document_type=document_type,
        department_id=department_id,
        is_active=is_active,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    items = [_to_summary(record, document_repository.get_chunk_count(db, record.id)) for record in records]
    return Page(items=items, page=page, page_size=page_size, total=total)


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(
    document_id: uuid.UUID,
    scope: UserScope = Depends(require_permission(GATE_PERMISSION)),
    db: Session = Depends(get_db),
) -> DocumentDetail:
    record = document_repository.get_document(db, document_id)
    if record is None or (scope.role is not Role.SUPER_ADMIN and record.hospital_id != scope.hospital_id):
        raise _not_found()
    return _to_detail(db, record)


@router.patch("/{document_id}", response_model=DocumentDetail)
def patch_document(
    document_id: uuid.UUID,
    payload: DocumentUpdateRequest,
    scope: UserScope = Depends(require_permission(GATE_PERMISSION)),
    db: Session = Depends(get_db),
) -> DocumentDetail:
    try:
        record = update_document(db, scope=scope, document_id=document_id, payload=payload)
    except DocumentNotFound as exc:
        raise _not_found() from exc
    except PermissionDenied as exc:
        raise _forbidden() from exc
    return _to_detail(db, record)


@router.post("/{document_id}/archive", response_model=DocumentDetail)
def archive(
    document_id: uuid.UUID,
    scope: UserScope = Depends(require_permission(GATE_PERMISSION)),
    db: Session = Depends(get_db),
) -> DocumentDetail:
    try:
        record = archive_document(db, scope=scope, document_id=document_id, actor_user_id=scope.user_id)
    except DocumentNotFound as exc:
        raise _not_found() from exc
    return _to_detail(db, record)
