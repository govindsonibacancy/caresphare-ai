from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.supabase_admin import SupabaseAdminClient, SupabaseAdminError, get_supabase_admin_client
from app.core.db import get_db
from app.schemas.auth import AuthenticatedUser
from app.schemas.invitations import AcceptInvitationRequest, InvitationPreview
from app.services import invitation_service
from app.services.invitation_service import (
    IdentityAlreadyProvisioned,
    IdentityVerificationFailed,
    InvitationNotAcceptable,
    InvitationNotFound,
)

router = APIRouter(prefix="/api/invitations", tags=["invitations"])


@router.get("/{token}", response_model=InvitationPreview)
def preview_invitation(token: str, db: Session = Depends(get_db)) -> InvitationPreview:
    """Public - the invitee has no account yet, so this can't require a JWT.
    Returns only what the accept-invitation page needs (masked email, name,
    role, department, status, expiry) - never the token hash or inviter.
    """
    try:
        return invitation_service.get_invitation_preview(db, token)
    except InvitationNotFound as exc:
        raise HTTPException(status_code=404, detail="Invitation not found.") from exc


@router.post("/{token}/accept", response_model=AuthenticatedUser, status_code=201)
def accept_invitation(
    token: str,
    payload: AcceptInvitationRequest,
    db: Session = Depends(get_db),
    admin_client: SupabaseAdminClient = Depends(get_supabase_admin_client),
) -> AuthenticatedUser:
    """Public in the same sense as POST /api/auth/register: the invitee has
    already created their own Supabase identity client-side (or signed in to
    an existing one) and supplies only its id, which is cross-verified
    against Supabase's Admin API before anything is provisioned - role,
    hospital, and department all come from the invitation record, never
    from this request.
    """
    try:
        record = invitation_service.accept_invitation(
            db, token=token, auth_user_id=payload.auth_user_id, admin_client=admin_client
        )
    except SupabaseAdminError as exc:
        raise HTTPException(
            status_code=503, detail="Invitation acceptance is temporarily unavailable. Please try again shortly."
        ) from exc
    except InvitationNotFound as exc:
        raise HTTPException(status_code=404, detail="Invitation not found.") from exc
    except InvitationNotAcceptable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IdentityAlreadyProvisioned as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IdentityVerificationFailed as exc:
        raise HTTPException(status_code=400, detail="Could not verify the provided identity.") from exc

    return AuthenticatedUser(**record.__dict__)
