import logging
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.audit import events as audit_events
from app.auth.dependencies import get_current_auth_user
from app.core.db import get_db
from app.permissions.authorization import authorize
from app.permissions.exceptions import PermissionDenied
from app.permissions.scope import UserScope, resolve_user_scope
from app.schemas.auth import AuthenticatedUser

logger = logging.getLogger(__name__)


def get_user_scope(
    current_user: AuthenticatedUser = Depends(get_current_auth_user),
    db: Session = Depends(get_db),
) -> UserScope:
    """The bare scope-resolution building block, with no permission check -
    for routes (Phase 6's clinical list/detail endpoints) that need to
    accept several possible permissions or run a resource-specific
    `authorize()` call themselves rather than gating on one fixed
    permission up front. `require_permission` below is the common case;
    this is for when a route's authorization can't be expressed as a single
    permission string at the dependency-injection level.
    """
    return resolve_user_scope(db, current_user)


def require_permission(permission: str) -> Callable[..., UserScope]:
    """FastAPI dependency factory: `Depends(require_permission("some_code"))`.

    Built on top of get_current_auth_user (Phase 3) rather than
    re-verifying the JWT - authentication and the active-account check stay
    exactly where Phase 3 put them. This only adds the permission check and
    hands the endpoint a resolved UserScope to run any further
    resource-specific authorization (can_access_*) against.

    A missing permission is a 403, never a 401 - the caller is genuinely
    authenticated, just not authorized for this action (see
    docs/AUTHORIZATION.md, "401 vs 403").

    Phase 15: a denial here is also audited (AUTHORIZATION_DENIED) - one
    change at this single, shared dependency instruments every
    permission-gated endpoint in the codebase at once, rather than adding
    an audit call at each of them individually (see
    docs/AUDIT_AND_OBSERVABILITY.md, "Authorization auditing"). Only the
    permission code being checked is recorded - never the resource that
    was being requested, never request body/query content, so a denial
    audit entry can never itself leak what was denied (see
    docs/AUDIT_AND_OBSERVABILITY.md, "Authorization denial must not leak
    information"). The audit write is best-effort (see
    app/audit/events.py) and never turns a clean 403 into a 500.
    """

    def dependency(
        request: Request,
        current_user: AuthenticatedUser = Depends(get_current_auth_user),
        db: Session = Depends(get_db),
    ) -> UserScope:
        scope = resolve_user_scope(db, current_user)
        try:
            authorize(db, scope, permission)
        except PermissionDenied as exc:
            request_id = getattr(request.state, "request_id", None)
            audit_events.record_event(
                db,
                actor_user_id=scope.user_id,
                action="AUTHORIZATION_DENIED",
                resource_type=None,
                hospital_id=scope.hospital_id,
                request_id=request_id,
                metadata={"permission": permission},
            )
            try:
                db.commit()
            except Exception:
                logger.warning("Failed to commit AUTHORIZATION_DENIED audit event.")
                db.rollback()
            raise HTTPException(
                status_code=403,
                detail="You do not have permission to perform this action.",
            ) from exc
        return scope

    return dependency
