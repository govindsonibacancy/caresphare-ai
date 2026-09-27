from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.permissions.dependencies import require_permission
from app.permissions.permissions import Permission
from app.permissions.scope import UserScope
from app.repositories import invitation_repository
from app.schemas.invitations import DepartmentSummary

router = APIRouter(prefix="/api/admin/departments", tags=["admin-invitations"])


@router.get("", response_model=list[DepartmentSummary])
def list_departments(
    scope: UserScope = Depends(require_permission(Permission.MANAGE_USERS)),
    db: Session = Depends(get_db),
) -> list[DepartmentSummary]:
    """Populates the employee-invitation form's department picker, scoped to
    the caller's own hospital - not a general department directory."""
    records = invitation_repository.list_departments(db, scope.hospital_id)
    return [DepartmentSummary(id=r.id, name=r.name, code=r.code) for r in records]
