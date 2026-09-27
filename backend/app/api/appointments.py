import datetime
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api._clinical_common import PageParam, PageSizeParam, authorize_resource, require_any_permission
from app.core.db import get_db
from app.permissions.authorization import ResourceKind
from app.permissions.dependencies import get_user_scope
from app.permissions.permissions import Permission
from app.permissions.scope import UserScope
from app.repositories import clinical_repository
from app.schemas.clinical import AppointmentResponse
from app.schemas.pagination import Page

router = APIRouter(prefix="/api/appointments", tags=["appointments"])

# PATIENT (own), DOCTOR/RECEPTIONIST/STAFF (create/manage implies view),
# HOSPITAL_ADMIN (manage). NURSE has none of these in the seed, matching
# can_access_appointment already denying NURSE - see docs/AUTHORIZATION.md.
LIST_PERMISSIONS = (
    Permission.VIEW_OWN_APPOINTMENTS,
    Permission.CREATE_APPOINTMENTS,
    Permission.MANAGE_APPOINTMENTS,
)

AppointmentStatus = Literal["SCHEDULED", "CONFIRMED", "COMPLETED", "CANCELLED", "NO_SHOW"]
SortBy = Literal["appointment_date", "created_at"]


@router.get("", response_model=Page[AppointmentResponse])
def list_appointments(
    page: int = PageParam,
    page_size: int = PageSizeParam,
    patient_id: uuid.UUID | None = Query(default=None),
    doctor_id: uuid.UUID | None = Query(default=None),
    department_id: uuid.UUID | None = Query(default=None),
    status: AppointmentStatus | None = Query(default=None),
    date_from: datetime.date | None = Query(default=None),
    date_to: datetime.date | None = Query(default=None),
    sort_by: SortBy = Query(default="appointment_date"),
    descending: bool = Query(default=True),
    scope: UserScope = Depends(get_user_scope),
    db: Session = Depends(get_db),
) -> Page[AppointmentResponse]:
    """Filters (`patient_id`, `doctor_id`, ...) narrow an already-authorized
    result set - they are ANDed onto the scope clause the repository
    builds from `scope`, never used in place of it. Requesting another
    patient's `patient_id` as a doctor with no assignment to them yields an
    empty page, not that patient's appointments - see
    docs/SECURE_DATA_APIS.md, "Query parameters must never expand access".
    """
    require_any_permission(scope, LIST_PERMISSIONS)
    records, total = clinical_repository.list_appointments(
        db,
        scope,
        limit=page_size,
        offset=(page - 1) * page_size,
        patient_id=patient_id,
        doctor_id=doctor_id,
        department_id=department_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
        sort_by=sort_by,
        sort_desc=descending,
    )
    return Page(
        items=[AppointmentResponse(**r.__dict__) for r in records], page=page, page_size=page_size, total=total
    )


@router.get("/{appointment_id}", response_model=AppointmentResponse)
def get_appointment(
    appointment_id: uuid.UUID,
    scope: UserScope = Depends(get_user_scope),
    db: Session = Depends(get_db),
) -> AppointmentResponse:
    authorize_resource(db, scope, LIST_PERMISSIONS, ResourceKind.APPOINTMENT, appointment_id)
    record = clinical_repository.get_appointment(db, appointment_id)
    assert record is not None
    return AppointmentResponse(**record.__dict__)
