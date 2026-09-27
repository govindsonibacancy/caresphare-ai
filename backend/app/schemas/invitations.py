import datetime
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr

# The only roles an employee invitation may create - PATIENT/HOSPITAL_ADMIN/
# SUPER_ADMIN are structurally impossible to submit here, not just
# filtered: Pydantic rejects anything outside this Literal with a 422 before
# any business logic runs. See app/services/invitation_service.py for the
# same set used to constrain what's shown as "inviteable" elsewhere.
InviteableRoleName = Literal["DOCTOR", "NURSE", "RECEPTIONIST", "STAFF"]


class CreateInvitationRequest(BaseModel):
    """What a hospital admin submits to invite an employee. Deliberately has
    no user_id/auth_user_id/permission_ids/status/invited_by_user_id/token -
    those are all server-derived. `hospital_id` is accepted only because
    SUPER_ADMIN is not bound to a single hospital (see
    resolve_invitation_hospital_id); for every other caller it must equal
    their own scope and is otherwise redundant with it.
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    first_name: str
    last_name: str
    role: InviteableRoleName
    department_id: uuid.UUID | None = None
    hospital_id: uuid.UUID | None = None


class InvitationSummary(BaseModel):
    """Admin-facing view (list/detail) - no token hash, no raw token."""

    id: uuid.UUID
    email: EmailStr
    first_name: str
    last_name: str
    role: str
    hospital_id: uuid.UUID
    department_id: uuid.UUID | None
    status: str
    invited_by_user_id: uuid.UUID
    expires_at: datetime.datetime
    accepted_at: datetime.datetime | None
    created_at: datetime.datetime


class InvitationCreatedResponse(InvitationSummary):
    """Only the create-invitation response includes the invitation URL (and
    therefore the raw token, as its query parameter) - shown once, to the
    authorized admin who just created it. It is never persisted, never
    included in InvitationSummary/InvitationPreview, and never logged
    outside DevelopmentEmailService.
    """

    invitation_url: str


class InvitationPreview(BaseModel):
    """The public, pre-acceptance view (GET /api/invitations/{token}) - only
    what the accept-invitation page needs to render, with the email
    partially masked. No internal ids, no hospital id, no inviter
    information.
    """

    email: str
    first_name: str
    last_name: str
    role: str
    department: str | None
    status: str
    expires_at: datetime.datetime


class DepartmentSummary(BaseModel):
    """For populating the invitation form's department picker - not a
    general department API."""

    id: uuid.UUID
    name: str
    code: str


class AcceptInvitationRequest(BaseModel):
    """The invitee has already created (or signed in to) their own Supabase
    identity client-side (mirroring patient registration - see
    app/api/auth.py) and supplies only that id. Role/hospital/department
    come from the invitation record itself, never from this request.
    """

    model_config = ConfigDict(extra="forbid")

    auth_user_id: uuid.UUID
