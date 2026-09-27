"""Minimal, hospital-scoped picker endpoints for the document-upload form's
"restrict to specific doctors/staff" controls (docs/RAG_INGESTION.md,
"Access-control metadata"). Distinct from the general Phase 6 `/api/doctors`
directory: this returns names (needed for a picker UI), gated on
`manage_hospital_documents` rather than `view_hospital_operations`, mirroring
Phase 5's purpose-built `/api/admin/departments`.
"""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.permissions.dependencies import require_permission
from app.permissions.permissions import Permission
from app.permissions.scope import UserScope

router = APIRouter(prefix="/api/admin", tags=["admin-documents"])


class DoctorOption(BaseModel):
    id: uuid.UUID
    first_name: str
    last_name: str
    specialization: str | None


class StaffOption(BaseModel):
    id: uuid.UUID
    first_name: str
    last_name: str
    designation: str


@router.get("/doctors", response_model=list[DoctorOption])
def list_doctor_options(
    scope: UserScope = Depends(require_permission(Permission.MANAGE_HOSPITAL_DOCUMENTS)),
    db: Session = Depends(get_db),
) -> list[DoctorOption]:
    rows = db.execute(
        text(
            "SELECT d.id, u.first_name, u.last_name, d.specialization "
            "FROM doctors d JOIN users u ON u.id = d.user_id "
            "WHERE d.hospital_id = :hospital_id ORDER BY u.last_name, u.first_name"
        ),
        {"hospital_id": scope.hospital_id},
    ).mappings()
    return [DoctorOption(**row) for row in rows]


@router.get("/staff", response_model=list[StaffOption])
def list_staff_options(
    scope: UserScope = Depends(require_permission(Permission.MANAGE_HOSPITAL_DOCUMENTS)),
    db: Session = Depends(get_db),
) -> list[StaffOption]:
    rows = db.execute(
        text(
            "SELECT s.id, u.first_name, u.last_name, s.designation "
            "FROM staff s JOIN users u ON u.id = s.user_id "
            "WHERE s.hospital_id = :hospital_id ORDER BY u.last_name, u.first_name"
        ),
        {"hospital_id": scope.hospital_id},
    ).mappings()
    return [StaffOption(**row) for row in rows]
