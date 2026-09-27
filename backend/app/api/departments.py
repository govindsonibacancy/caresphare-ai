import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api._clinical_common import PageParam, PageSizeParam, authorize_resource, require_any_permission
from app.core.db import get_db
from app.permissions.authorization import ResourceKind
from app.permissions.dependencies import get_user_scope
from app.permissions.permissions import Permission
from app.permissions.scope import UserScope
from app.repositories import clinical_repository
from app.schemas.clinical import DepartmentResponse
from app.schemas.pagination import Page

router = APIRouter(prefix="/api/departments", tags=["departments"])

# General, hospital-scoped department directory - distinct from
# /api/admin/departments (Phase 5), which is manage_users-gated and exists
# only to populate the employee-invitation form. This one is read access
# for hospital operations, same permission as the doctor directory.
LIST_PERMISSIONS = (Permission.VIEW_HOSPITAL_OPERATIONS,)


@router.get("", response_model=Page[DepartmentResponse])
def list_departments(
    page: int = PageParam,
    page_size: int = PageSizeParam,
    scope: UserScope = Depends(get_user_scope),
    db: Session = Depends(get_db),
) -> Page[DepartmentResponse]:
    require_any_permission(scope, LIST_PERMISSIONS)
    records, total = clinical_repository.list_hospital_departments(
        db, scope, limit=page_size, offset=(page - 1) * page_size
    )
    return Page(
        items=[DepartmentResponse(**r.__dict__) for r in records], page=page, page_size=page_size, total=total
    )


@router.get("/{department_id}", response_model=DepartmentResponse)
def get_department(
    department_id: uuid.UUID,
    scope: UserScope = Depends(get_user_scope),
    db: Session = Depends(get_db),
) -> DepartmentResponse:
    authorize_resource(db, scope, LIST_PERMISSIONS, ResourceKind.DEPARTMENT, department_id)
    record = clinical_repository.get_department(db, department_id)
    assert record is not None
    return DepartmentResponse(**record.__dict__)
