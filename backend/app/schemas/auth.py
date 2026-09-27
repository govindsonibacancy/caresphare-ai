import uuid

from pydantic import BaseModel, ConfigDict, EmailStr


class RegisterRequest(BaseModel):
    """Patient self-registration only. Deliberately has no role/hospital_id/
    permission fields - there is nothing here for a client to escalate with,
    and unexpected fields (e.g. a submitted "role") are rejected outright
    rather than silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    auth_user_id: uuid.UUID
    email: EmailStr
    first_name: str
    last_name: str


class AuthenticatedUser(BaseModel):
    """Safe application identity - never includes a password, token, or
    other credential material."""

    id: uuid.UUID
    auth_user_id: uuid.UUID
    email: EmailStr
    first_name: str
    last_name: str
    hospital_id: uuid.UUID
    role: str
    department_id: uuid.UUID | None
    is_active: bool
