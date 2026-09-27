import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.permissions.dependencies import require_permission
from app.permissions.exceptions import PermissionDenied
from app.permissions.permissions import Permission
from app.permissions.scope import UserScope
from app.schemas.invitations import CreateInvitationRequest, InvitationCreatedResponse, InvitationSummary
from app.services import invitation_service
from app.services.invitation_service import DuplicateInvitation, InvitationNotAcceptable, InvitationNotFound

router = APIRouter(prefix="/api/admin/invitations", tags=["admin-invitations"])


def _to_summary(record) -> InvitationSummary:
    return InvitationSummary(
        id=record.id,
        email=record.email,
        first_name=record.first_name,
        last_name=record.last_name,
        role=record.role,
        hospital_id=record.hospital_id,
        department_id=record.department_id,
        status=record.status,
        invited_by_user_id=record.invited_by_user_id,
        expires_at=record.expires_at,
        accepted_at=record.accepted_at,
        created_at=record.created_at,
    )


@router.post("", response_model=InvitationCreatedResponse, status_code=201)
def create_invitation(
    payload: CreateInvitationRequest,
    scope: UserScope = Depends(require_permission(Permission.MANAGE_USERS)),
    db: Session = Depends(get_db),
) -> InvitationCreatedResponse:
    try:
        record, invitation_url = invitation_service.create_invitation(
            db, scope=scope, inviter_user_id=scope.user_id, payload=payload
        )
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail="You do not have permission to perform this action.") from exc
    except DuplicateInvitation as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return InvitationCreatedResponse(**_to_summary(record).model_dump(), invitation_url=invitation_url)


@router.get("", response_model=list[InvitationSummary])
def list_invitations(
    status: str | None = Query(default=None),
    role: str | None = Query(default=None),
    department_id: uuid.UUID | None = Query(default=None),
    email: str | None = Query(default=None),
    hospital_id: uuid.UUID | None = Query(default=None),
    scope: UserScope = Depends(require_permission(Permission.MANAGE_USERS)),
    db: Session = Depends(get_db),
) -> list[InvitationSummary]:
    try:
        records = invitation_service.list_invitations_for_scope(
            db,
            scope=scope,
            hospital_id=hospital_id,
            status=status,
            role=role,
            department_id=department_id,
            email=email,
        )
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail="You do not have permission to perform this action.") from exc

    return [_to_summary(record) for record in records]


@router.post("/{invitation_id}/revoke", response_model=InvitationSummary)
def revoke_invitation(
    invitation_id: uuid.UUID,
    scope: UserScope = Depends(require_permission(Permission.MANAGE_USERS)),
    db: Session = Depends(get_db),
) -> InvitationSummary:
    try:
        record = invitation_service.revoke_invitation(db, scope=scope, invitation_id=invitation_id)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail="You do not have permission to perform this action.") from exc
    except InvitationNotFound as exc:
        raise HTTPException(status_code=404, detail="Invitation not found.") from exc
    except InvitationNotAcceptable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _to_summary(record)
