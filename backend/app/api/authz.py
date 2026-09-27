from fastapi import APIRouter, Depends

from app.permissions.dependencies import require_permission
from app.permissions.permissions import Permission
from app.permissions.scope import UserScope
from app.schemas.authorization import AuthorizationContext

router = APIRouter(prefix="/api/authz", tags=["authz"])


@router.get("/me", response_model=AuthorizationContext)
def read_authorization_context(
    scope: UserScope = Depends(require_permission(Permission.VIEW_OWN_PROFILE)),
) -> AuthorizationContext:
    """A small, deliberately non-sensitive view of the caller's own
    authorization scope - role, hospital/department, and permission list -
    entirely derived server-side from the verified JWT and the database.
    Takes no input from the client (no body, no query params consulted), so
    there is nothing here for a client to manipulate into a different
    answer. Requires view_own_profile, which every role has, so this
    endpoint itself only fails authorization if the account is inactive/
    unprovisioned (handled upstream) or that one permission mapping is
    removed (see backend/tests/test_authz_endpoint.py's mutation test).

    This is Phase 4's internal verification surface for the permission
    engine, per the phase's instructions - not a clinical data API. Clinical
    resource authorization (can_access_patient et al.) is exercised directly
    by backend/tests/test_resource_authorization.py, not through HTTP.
    """
    return AuthorizationContext(
        role=scope.role.value,
        hospital_id=scope.hospital_id,
        department_id=scope.department_id,
        permissions=sorted(scope.permissions),
    )
