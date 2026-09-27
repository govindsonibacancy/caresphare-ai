"""Shared plumbing for the Phase 6 clinical-data routers
(patients/doctors/departments/appointments/medical_records/lab_reports/
prescriptions). Not a router itself - imported by each of those.
"""

from collections.abc import Iterable

from fastapi import HTTPException, Query
from sqlalchemy.orm import Session

from app.permissions.authorization import ResourceKind, authorize, has_permission
from app.permissions.exceptions import PermissionDenied
from app.permissions.scope import UserScope
from app.schemas.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

# FastAPI's own ge/le validation on these gives a 422 for an invalid or
# oversized page_size before any query runs - see
# docs/SECURE_DATA_APIS.md, "Pagination".
PageParam = Query(default=1, ge=1, description="1-indexed page number")
PageSizeParam = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Rows per page")


def require_any_permission(scope: UserScope, permissions: Iterable[str]) -> None:
    """The list-endpoint half of authorization: permission only, no single
    resource id to check yet (list_* bakes the resource scope into its own
    SQL - see clinical_repository.py). Raises the same generic 403
    require_permission does.
    """
    if not has_permission(scope, permissions):
        raise HTTPException(status_code=403, detail="You do not have permission to perform this action.")


def authorize_resource(
    db: Session,
    scope: UserScope,
    permission: str | Iterable[str],
    kind: ResourceKind,
    resource_id,
) -> None:
    """The detail-endpoint half: permission AND the specific resource's
    relationship to `scope`, via the exact same authorize()/can_access_*
    Phase 4 already established - see docs/AUTHORIZATION.md.

    Deliberately raises the same generic 403 whether the resource doesn't
    exist or exists but is outside the caller's scope - see
    docs/SECURE_DATA_APIS.md, "Enumeration protection".
    """
    try:
        authorize(db, scope, permission, resource=(kind, resource_id))
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail="You do not have permission to perform this action.") from exc
