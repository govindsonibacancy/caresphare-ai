import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api._clinical_common import PageParam, PageSizeParam, authorize_resource, require_any_permission
from app.core.db import get_db
from app.permissions.authorization import ResourceKind
from app.permissions.dependencies import get_user_scope
from app.permissions.permissions import Permission
from app.permissions.scope import UserScope
from app.repositories import clinical_repository
from app.schemas.clinical import DoctorResponse
from app.schemas.pagination import Page

router = APIRouter(prefix="/api/doctors", tags=["doctors"])

# A doctor directory is operational/administrative visibility, not clinical
# access - view_hospital_operations is the seeded permission that fits, and
# it's held by RECEPTIONIST/STAFF/HOSPITAL_ADMIN/SUPER_ADMIN, not DOCTOR/
# NURSE/PATIENT. See docs/SECURE_DATA_APIS.md, "Doctors".
LIST_PERMISSIONS = (Permission.VIEW_HOSPITAL_OPERATIONS,)


@router.get("", response_model=Page[DoctorResponse])
def list_doctors(
    page: int = PageParam,
    page_size: int = PageSizeParam,
    department_id: uuid.UUID | None = Query(default=None),
    scope: UserScope = Depends(get_user_scope),
    db: Session = Depends(get_db),
) -> Page[DoctorResponse]:
    require_any_permission(scope, LIST_PERMISSIONS)
    records, total = clinical_repository.list_doctors(
        db, scope, limit=page_size, offset=(page - 1) * page_size, department_id=department_id
    )
    return Page(items=[DoctorResponse(**r.__dict__) for r in records], page=page, page_size=page_size, total=total)


@router.get("/{doctor_id}", response_model=DoctorResponse)
def get_doctor(
    doctor_id: uuid.UUID,
    scope: UserScope = Depends(get_user_scope),
    db: Session = Depends(get_db),
) -> DoctorResponse:
    authorize_resource(db, scope, LIST_PERMISSIONS, ResourceKind.DOCTOR, doctor_id)
    record = clinical_repository.get_doctor(db, doctor_id)
    assert record is not None
    return DoctorResponse(**record.__dict__)
