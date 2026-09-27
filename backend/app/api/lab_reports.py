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
from app.schemas.clinical import LabReportResponse
from app.schemas.pagination import Page

router = APIRouter(prefix="/api/lab-reports", tags=["lab-reports"])

# view_own_reports (PATIENT, own only) is the only seeded permission naming
# lab reports specifically. The seed has no dedicated "view lab reports"
# permission for DOCTOR - view_patient_medical_records is reused as the
# umbrella clinical-data permission for a doctor's assigned patients here
# (a hospital's "medical record" for a patient reasonably includes their
# lab results), rather than leaving doctors with no path to lab data at
# all. Documented explicitly, not a silent addition - see
# docs/SECURE_DATA_APIS.md, "Lab reports".
LIST_PERMISSIONS = (Permission.VIEW_OWN_REPORTS, Permission.VIEW_PATIENT_MEDICAL_RECORDS)

LabReportStatus = Literal["ORDERED", "IN_PROGRESS", "COMPLETED", "CANCELLED"]


@router.get("", response_model=Page[LabReportResponse])
def list_lab_reports(
    page: int = PageParam,
    page_size: int = PageSizeParam,
    patient_id: uuid.UUID | None = Query(default=None),
    status: LabReportStatus | None = Query(default=None),
    scope: UserScope = Depends(get_user_scope),
    db: Session = Depends(get_db),
) -> Page[LabReportResponse]:
    require_any_permission(scope, LIST_PERMISSIONS)
    records, total = clinical_repository.list_lab_reports(
        db, scope, limit=page_size, offset=(page - 1) * page_size, patient_id=patient_id, status=status
    )
    return Page(
        items=[LabReportResponse(**r.__dict__) for r in records], page=page, page_size=page_size, total=total
    )


@router.get("/{report_id}", response_model=LabReportResponse)
def get_lab_report(
    report_id: uuid.UUID,
    scope: UserScope = Depends(get_user_scope),
    db: Session = Depends(get_db),
) -> LabReportResponse:
    authorize_resource(db, scope, LIST_PERMISSIONS, ResourceKind.LAB_REPORT, report_id)
    record = clinical_repository.get_lab_report(db, report_id)
    assert record is not None

    audit_events.record_event(
        db,
        actor_user_id=scope.user_id,
        action="LAB_REPORT_ACCESSED",
        resource_type="lab_reports",
        resource_id=report_id,
        hospital_id=record.hospital_id,
    )
    db.commit()

    return LabReportResponse(**record.__dict__)
