import uuid

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
from app.schemas.clinical import PrescriptionResponse
from app.schemas.pagination import Page

router = APIRouter(prefix="/api/prescriptions", tags=["prescriptions"])

# Same reasoning as lab_reports.py: view_own_prescriptions covers PATIENT's
# own; view_patient_medical_records is reused as DOCTOR's umbrella clinical
# permission (the seed has no dedicated "view prescriptions" permission).
# See docs/SECURE_DATA_APIS.md, "Prescriptions".
LIST_PERMISSIONS = (Permission.VIEW_OWN_PRESCRIPTIONS, Permission.VIEW_PATIENT_MEDICAL_RECORDS)


@router.get("", response_model=Page[PrescriptionResponse])
def list_prescriptions(
    page: int = PageParam,
    page_size: int = PageSizeParam,
    patient_id: uuid.UUID | None = Query(default=None),
    doctor_id: uuid.UUID | None = Query(default=None),
    scope: UserScope = Depends(get_user_scope),
    db: Session = Depends(get_db),
) -> Page[PrescriptionResponse]:
    require_any_permission(scope, LIST_PERMISSIONS)
    records, total = clinical_repository.list_prescriptions(
        db, scope, limit=page_size, offset=(page - 1) * page_size, patient_id=patient_id, doctor_id=doctor_id
    )
    return Page(
        items=[PrescriptionResponse(**r.__dict__) for r in records], page=page, page_size=page_size, total=total
    )


@router.get("/{prescription_id}", response_model=PrescriptionResponse)
def get_prescription(
    prescription_id: uuid.UUID,
    scope: UserScope = Depends(get_user_scope),
    db: Session = Depends(get_db),
) -> PrescriptionResponse:
    authorize_resource(db, scope, LIST_PERMISSIONS, ResourceKind.PRESCRIPTION, prescription_id)
    record = clinical_repository.get_prescription(db, prescription_id)
    assert record is not None

    audit_events.record_event(
        db,
        actor_user_id=scope.user_id,
        action="PRESCRIPTION_ACCESSED",
        resource_type="prescriptions",
        resource_id=prescription_id,
        hospital_id=record.hospital_id,
    )
    db.commit()

    return PrescriptionResponse(**record.__dict__)
