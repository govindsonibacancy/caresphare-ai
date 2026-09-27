import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api._clinical_common import PageParam, PageSizeParam, authorize_resource, require_any_permission
from app.audit import events as audit_events
from app.core.db import get_db
from app.permissions.authorization import ResourceKind
from app.permissions.dependencies import get_user_scope
from app.permissions.permissions import Permission
from app.permissions.scope import UserScope
from app.repositories import clinical_repository
from app.schemas.clinical import PatientResponse
from app.schemas.pagination import Page

router = APIRouter(prefix="/api/patients", tags=["patients"])

# DOCTOR and NURSE hold both; RECEPTIONIST holds only the basic-information
# one. Neither PATIENT, STAFF, HOSPITAL_ADMIN, nor SUPER_ADMIN has either in
# the seed, so they get 403 here - see docs/SECURE_DATA_APIS.md, "Patients".
LIST_PERMISSIONS = (Permission.VIEW_ASSIGNED_PATIENTS, Permission.VIEW_PATIENT_BASIC_INFORMATION)


@router.get("", response_model=Page[PatientResponse])
def list_patients(
    page: int = PageParam,
    page_size: int = PageSizeParam,
    scope: UserScope = Depends(get_user_scope),
    db: Session = Depends(get_db),
) -> Page[PatientResponse]:
    require_any_permission(scope, LIST_PERMISSIONS)
    records, total = clinical_repository.list_patients(db, scope, limit=page_size, offset=(page - 1) * page_size)
    return Page(items=[PatientResponse(**r.__dict__) for r in records], page=page, page_size=page_size, total=total)


@router.get("/{patient_id}", response_model=PatientResponse)
def get_patient(
    patient_id: uuid.UUID,
    scope: UserScope = Depends(get_user_scope),
    db: Session = Depends(get_db),
) -> PatientResponse:
    authorize_resource(db, scope, LIST_PERMISSIONS, ResourceKind.PATIENT, patient_id)
    record = clinical_repository.get_patient(db, patient_id)
    assert record is not None  # authorize_resource already proved it exists and is in scope

    audit_events.record_event(
        db,
        actor_user_id=scope.user_id,
        action="PATIENT_RECORD_ACCESSED",
        resource_type="patients",
        resource_id=patient_id,
        hospital_id=record.hospital_id,
    )
    db.commit()

    return PatientResponse(**record.__dict__)
