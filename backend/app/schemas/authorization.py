import uuid

from pydantic import BaseModel


class AuthorizationContext(BaseModel):
    """What the frontend needs to make UX-only permission decisions (hide a
    nav link, disable a button). Deliberately excludes patient_id/doctor_id/
    staff_id and anything else not needed for that - this is not a general
    identity dump (GET /api/auth/me already covers identity).
    """

    role: str
    hospital_id: uuid.UUID
    department_id: uuid.UUID | None
    permissions: list[str]
