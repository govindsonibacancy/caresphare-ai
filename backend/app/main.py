from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin_departments import router as admin_departments_router
from app.api.admin_directory import router as admin_directory_router
from app.api.admin_documents import router as admin_documents_router
from app.api.admin_invitations import router as admin_invitations_router
from app.api.appointments import router as appointments_router
from app.api.auth import router as auth_router
from app.api.authz import router as authz_router
from app.api.conversations import router as conversations_router
from app.api.departments import router as departments_router
from app.api.doctors import router as doctors_router
from app.api.health import router as health_router
from app.api.invitations import router as invitations_router
from app.api.lab_reports import router as lab_reports_router
from app.api.medical_records import router as medical_records_router
from app.api.patients import router as patients_router
from app.api.prescriptions import router as prescriptions_router
from app.api.rag import router as rag_router
from app.core.config import get_settings
from app.core.request_context import RequestIDMiddleware

settings = get_settings()

app = FastAPI(title=settings.app_name)

# Order matters: CORS added last runs first (Starlette middleware wraps in
# reverse-registration order), so the request id is already on
# request.state before CORS/routing see it, and the X-Request-ID response
# header survives on every response, including CORS preflight/error ones.
#
# expose_headers (Phase 16): without this, a cross-origin browser response
# hides every custom response header from JS by default, per the Fetch
# spec's CORS-safelisted-response-header-name list - X-Request-ID would be
# sent but silently unreadable via `response.headers.get(...)` in the
# frontend. Safe to expose: it is a correlation id, not a secret (see
# docs/AUDIT_AND_OBSERVABILITY.md, "Request/correlation ID") - this does
# not change what the header contains or who can set it, only whether an
# already-cross-origin-permitted browser page may read it back.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(RequestIDMiddleware)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(authz_router)
app.include_router(admin_invitations_router)
app.include_router(admin_departments_router)
app.include_router(admin_directory_router)
app.include_router(admin_documents_router)
app.include_router(invitations_router)
app.include_router(patients_router)
app.include_router(doctors_router)
app.include_router(departments_router)
app.include_router(appointments_router)
app.include_router(medical_records_router)
app.include_router(lab_reports_router)
app.include_router(prescriptions_router)
app.include_router(rag_router)
app.include_router(conversations_router)
