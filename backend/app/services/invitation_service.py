"""Employee invitation lifecycle: create -> accept -> provision, plus list/
revoke. See docs/EMPLOYEE_INVITATIONS.md for the full model.

Exceptions raised here are plain Python (not HTTPException) - the API layer
(app/api/admin_invitations.py, app/api/invitations.py) translates them to
HTTP responses, the same isolation pattern app/permissions/exceptions.py
uses for PermissionDenied.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import events as audit_events
from app.auth.invitation_tokens import generate_invitation_token, hash_invitation_token
from app.auth.supabase_admin import SupabaseAdminClient
from app.core.config import get_settings
from app.permissions.authorization import resolve_hospital_scope
from app.permissions.exceptions import PermissionDenied
from app.permissions.roles import Role
from app.permissions.scope import UserScope
from app.repositories import invitation_repository, user_repository
from app.repositories.invitation_repository import InvitationRecord
from app.repositories.user_repository import UserRecord
from app.schemas.invitations import CreateInvitationRequest, InvitationPreview
from app.services.email import get_email_service

INVITABLE_ROLES = frozenset({Role.DOCTOR, Role.NURSE, Role.RECEPTIONIST, Role.STAFF})


class InvitationNotFound(Exception):
    pass


class InvitationNotAcceptable(Exception):
    """Invitation exists but isn't in a state that permits the requested
    operation (already accepted/expired/revoked)."""


class DuplicateInvitation(Exception):
    pass


class IdentityAlreadyProvisioned(Exception):
    pass


class IdentityVerificationFailed(Exception):
    pass


def _mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}{'*' * max(len(local) - len(visible), 1)}@{domain}"


def create_invitation(
    db: Session,
    *,
    scope: UserScope,
    inviter_user_id: uuid.UUID,
    payload: CreateInvitationRequest,
) -> tuple[InvitationRecord, str]:
    """Returns (record, invitation_url). The URL (and therefore the raw
    token) is returned only here, to the admin who just made this call -
    see schemas.invitations.InvitationCreatedResponse.
    """
    hospital_id = resolve_hospital_scope(scope, payload.hospital_id)

    role = Role(payload.role)  # payload.role is Pydantic-Literal-constrained to INVITABLE_ROLES already
    if role not in INVITABLE_ROLES:
        raise PermissionDenied(f"role {role.value} is not inviteable")  # defensive; unreachable via the schema

    department_id = None
    if payload.department_id is not None:
        if not invitation_repository.department_belongs_to_hospital(db, payload.department_id, hospital_id):
            raise PermissionDenied("department does not belong to the target hospital")
        department_id = payload.department_id

    if user_repository.email_in_use(db, payload.email):
        raise DuplicateInvitation("a user with this email already exists")

    role_id = invitation_repository.get_role_id_by_name(db, role.value)
    if role_id is None:
        raise LookupError(f"seeded role {role.value!r} not found")

    raw_token = generate_invitation_token()
    token_hash = hash_invitation_token(raw_token)
    expires_at = datetime.now(UTC) + timedelta(days=get_settings().employee_invitation_expiry_days)

    try:
        with db.begin_nested():
            record = invitation_repository.create_invitation(
                db,
                token_hash=token_hash,
                email=payload.email,
                first_name=payload.first_name,
                last_name=payload.last_name,
                hospital_id=hospital_id,
                department_id=department_id,
                role_id=role_id,
                invited_by_user_id=inviter_user_id,
                expires_at=expires_at,
            )
    except IntegrityError as exc:
        raise DuplicateInvitation("an invitation is already pending for this email") from exc

    audit_events.record_event(
        db,
        actor_user_id=inviter_user_id,
        action="USER_INVITED",
        resource_type="employee_invitations",
        resource_id=record.id,
        hospital_id=hospital_id,
        metadata={"email": payload.email, "role": role.value, "department_id": str(department_id) if department_id else None},
    )
    db.commit()

    invitation_url = f"{get_settings().frontend_base_url}/accept-invitation?token={raw_token}"
    hospital_name = invitation_repository.get_hospital_name(db, hospital_id)
    get_email_service().send_employee_invitation(
        to_email=payload.email,
        first_name=payload.first_name,
        role=role.value,
        hospital_name=hospital_name,
        invitation_url=invitation_url,
    )

    return record, invitation_url


def get_invitation_preview(db: Session, token: str) -> InvitationPreview:
    invitation_repository.expire_stale_pending(db)
    db.commit()

    invitation = invitation_repository.get_invitation_by_token_hash(db, hash_invitation_token(token))
    if invitation is None:
        raise InvitationNotFound("invitation not found")

    return InvitationPreview(
        email=_mask_email(invitation.email),
        first_name=invitation.first_name,
        last_name=invitation.last_name,
        role=invitation.role,
        department=invitation.department_name,
        status=invitation.status,
        expires_at=invitation.expires_at,
    )


def accept_invitation(
    db: Session,
    *,
    token: str,
    auth_user_id: uuid.UUID,
    admin_client: SupabaseAdminClient,
) -> UserRecord:
    """Locks the invitation row for the duration of this transaction (see
    get_invitation_by_token_hash_for_update), so two concurrent accept
    attempts for the same token cannot both succeed - the second blocks
    until the first commits, then observes the now-non-PENDING status.
    """
    token_hash = hash_invitation_token(token)
    invitation = invitation_repository.get_invitation_by_token_hash_for_update(db, token_hash)
    if invitation is None:
        raise InvitationNotFound("invitation not found")

    now = datetime.now(UTC)
    if invitation.status == "PENDING" and invitation.expires_at < now:
        invitation_repository.mark_expired(db, invitation.id)
        db.commit()
        raise InvitationNotAcceptable("invitation has expired")
    if invitation.status != "PENDING":
        raise InvitationNotAcceptable(f"invitation is already {invitation.status.lower()}")

    if user_repository.get_user_by_auth_id(db, auth_user_id) is not None:
        raise IdentityAlreadyProvisioned("this identity is already provisioned")

    # Never trust the client-supplied auth_user_id at face value - same
    # cross-check pattern as patient registration (app/api/auth.py).
    supabase_user = admin_client.get_user_by_id(str(auth_user_id))
    if supabase_user is None or supabase_user.email.lower() != invitation.email.lower():
        raise IdentityVerificationFailed("could not verify the provided identity")

    user_id = user_repository.create_employee_user(
        db,
        auth_user_id=auth_user_id,
        email=invitation.email,
        first_name=invitation.first_name,
        last_name=invitation.last_name,
        hospital_id=invitation.hospital_id,
        department_id=invitation.department_id,
        role_id=invitation.role_id,
    )

    role = Role(invitation.role)
    if role is Role.DOCTOR:
        user_repository.create_doctor_profile(
            db, user_id=user_id, hospital_id=invitation.hospital_id, department_id=invitation.department_id
        )
    else:
        user_repository.create_staff_profile(
            db,
            user_id=user_id,
            hospital_id=invitation.hospital_id,
            department_id=invitation.department_id,
            designation=role.value,
        )

    invitation_repository.mark_accepted(db, invitation.id, user_id)
    audit_events.record_event(
        db,
        actor_user_id=user_id,
        action="INVITATION_ACCEPTED",
        resource_type="employee_invitations",
        resource_id=invitation.id,
        hospital_id=invitation.hospital_id,
        metadata={"role": role.value},
    )
    db.commit()

    record = user_repository.get_user_by_auth_id(db, auth_user_id)
    assert record is not None
    return record


def revoke_invitation(db: Session, *, scope: UserScope, invitation_id: uuid.UUID) -> InvitationRecord:
    invitation = invitation_repository.get_invitation_by_id(db, invitation_id)
    if invitation is None:
        raise InvitationNotFound("invitation not found")
    if scope.role is not Role.SUPER_ADMIN and invitation.hospital_id != scope.hospital_id:
        raise PermissionDenied("invitation is outside the caller's authorized hospital")
    if invitation.status != "PENDING":
        raise InvitationNotAcceptable(f"invitation is already {invitation.status.lower()}")

    invitation_repository.mark_revoked(db, invitation_id)
    audit_events.record_event(
        db,
        actor_user_id=scope.user_id,
        action="INVITATION_REVOKED",
        resource_type="employee_invitations",
        resource_id=invitation_id,
        hospital_id=invitation.hospital_id,
        metadata={"email": invitation.email},
    )
    db.commit()

    record = invitation_repository.get_invitation_by_id(db, invitation_id)
    assert record is not None
    return record


def list_invitations_for_scope(
    db: Session,
    *,
    scope: UserScope,
    hospital_id: uuid.UUID | None = None,
    status: str | None = None,
    role: str | None = None,
    department_id: uuid.UUID | None = None,
    email: str | None = None,
) -> list[InvitationRecord]:
    invitation_repository.expire_stale_pending(db)
    db.commit()

    target_hospital_id = resolve_hospital_scope(scope, hospital_id)
    return invitation_repository.list_invitations(
        db,
        hospital_id=target_hospital_id,
        status=status,
        role=role,
        department_id=department_id,
        email=email,
    )
