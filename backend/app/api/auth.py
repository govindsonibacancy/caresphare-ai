from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.audit import events as audit_events
from app.auth.dependencies import get_current_auth_user
from app.auth.supabase_admin import SupabaseAdminClient, SupabaseAdminError, get_supabase_admin_client
from app.core.config import get_settings
from app.core.db import get_db
from app.repositories import user_repository
from app.schemas.auth import AuthenticatedUser, RegisterRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=AuthenticatedUser, status_code=201)
def register_patient(
    payload: RegisterRequest,
    db: Session = Depends(get_db),
    admin_client: SupabaseAdminClient = Depends(get_supabase_admin_client),
) -> AuthenticatedUser:
    """Public patient self-registration.

    `auth_user_id` is never trusted at face value: it's confirmed against a
    real Supabase identity (via the service-role Admin API) whose email
    matches what the client claims, before any row is created. The role
    (PATIENT) and hospital are decided entirely server-side - see
    RegisterRequest, which has no field a client could use to request a
    different one.
    """
    try:
        supabase_user = admin_client.get_user_by_id(str(payload.auth_user_id))
    except SupabaseAdminError as exc:
        raise HTTPException(
            status_code=503,
            detail="Registration is temporarily unavailable. Please try again shortly.",
        ) from exc

    if supabase_user is None or supabase_user.email.lower() != payload.email.lower():
        raise HTTPException(status_code=400, detail="Could not verify the provided identity.")

    if user_repository.email_in_use(db, payload.email) or user_repository.get_user_by_auth_id(
        db, payload.auth_user_id
    ):
        raise HTTPException(status_code=409, detail="An account already exists for this identity.")

    settings = get_settings()
    try:
        hospital_id = user_repository.get_default_patient_hospital_id(
            db, settings.default_patient_hospital_code
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=503, detail="Patient registration is not available right now."
        ) from exc

    record = user_repository.create_patient_registration(
        db,
        auth_user_id=payload.auth_user_id,
        email=payload.email,
        first_name=payload.first_name,
        last_name=payload.last_name,
        hospital_id=hospital_id,
    )
    audit_events.record_event(
        db,
        actor_user_id=record.id,
        action="USER_CREATED",
        resource_type="users",
        resource_id=record.id,
        hospital_id=hospital_id,
        metadata={"role": "PATIENT", "via": "self_registration"},
    )
    db.commit()
    return AuthenticatedUser(**record.__dict__)


@router.get("/me", response_model=AuthenticatedUser)
def read_current_user(
    current_user: AuthenticatedUser = Depends(get_current_auth_user),
) -> AuthenticatedUser:
    return current_user
