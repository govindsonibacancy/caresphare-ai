import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api._clinical_common import PageParam, PageSizeParam, authorize_resource, require_any_permission
from app.audit import events as audit_events
from app.core.db import get_db
from app.permissions.authorization import ResourceKind
from app.permissions.dependencies import get_user_scope
from app.permissions.permissions import Permission
from app.permissions.scope import UserScope
from app.repositories import clinical_repository
from app.schemas.clinical import MedicalRecordResponse
from app.schemas.pagination import Page

router = APIRouter(prefix="/api/medical-records", tags=["medical-records"])

# view_patient_medical_records is the only seeded permission for this
# resource, held only by DOCTOR. PATIENT has no "view own medical records"
# permission in the seed - a patient sees their own appointments/lab
# reports/prescriptions but not the medical_records resource itself, an
# intentional, documented consequence of the existing permission matrix,
# not a defect introduced here - see docs/SECURE_DATA_APIS.md,
# "Medical records" and docs/AUTHORIZATION.md.
LIST_PERMISSIONS = (Permission.VIEW_PATIENT_MEDICAL_RECORDS,)

RecordType = Literal["CONSULTATION", "DIAGNOSIS", "FOLLOW_UP", "DISCHARGE", "CLINICAL_NOTE"]


@router.get("", response_model=Page[MedicalRecordResponse])
def list_medical_records(
    page: int = PageParam,
    page_size: int = PageSizeParam,
    patient_id: uuid.UUID | None = Query(default=None),
    doctor_id: uuid.UUID | None = Query(default=None),
    record_type: RecordType | None = Query(default=None),
    scope: UserScope = Depends(get_user_scope),
    db: Session = Depends(get_db),
) -> Page[MedicalRecordResponse]:
    require_any_permission(scope, LIST_PERMISSIONS)
    records, total = clinical_repository.list_medical_records(
        db,
        scope,
        limit=page_size,
        offset=(page - 1) * page_size,
        patient_id=patient_id,
        doctor_id=doctor_id,
        record_type=record_type,
    )
    return Page(
        items=[MedicalRecordResponse(**r.__dict__) for r in records], page=page, page_size=page_size, total=total
    )


@router.get("/{record_id}", response_model=MedicalRecordResponse)
def get_medical_record(
    record_id: uuid.UUID,
    scope: UserScope = Depends(get_user_scope),
    db: Session = Depends(get_db),
) -> MedicalRecordResponse:
    authorize_resource(db, scope, LIST_PERMISSIONS, ResourceKind.MEDICAL_RECORD, record_id)
    record = clinical_repository.get_medical_record(db, record_id)
    assert record is not None

    audit_events.record_event(
        db,
        actor_user_id=scope.user_id,
        action="MEDICAL_RECORD_ACCESSED",
        resource_type="medical_records",
        resource_id=record_id,
        hospital_id=record.hospital_id,
    )
    db.commit()

    return MedicalRecordResponse(**record.__dict__)
