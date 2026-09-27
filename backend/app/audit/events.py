import json
import logging
import uuid

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def record_event(
    session: Session,
    *,
    actor_user_id: uuid.UUID | None,
    action: str,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    hospital_id: uuid.UUID | None = None,
    request_id: uuid.UUID | None = None,
    metadata: dict | None = None,
) -> None:
    """Writes one row to audit_logs (the schema Phase 2 created; `request_id`
    added by Phase 15, see database/migrations/0020_audit_logs_request_id.sql).

    Never pass passwords, tokens, or full record bodies in `metadata` - only
    reference resources by id, per database/README.md's audit policy and
    docs/AUDIT_AND_OBSERVABILITY.md's data-minimization rules.

    Used by patient self-registration (USER_CREATED, Phase 3), employee
    invitations (USER_INVITED/INVITATION_ACCEPTED/INVITATION_REVOKED,
    Phase 5), sensitive clinical-record access
    (PATIENT_RECORD_ACCESSED/MEDICAL_RECORD_ACCESSED/LAB_REPORT_ACCESSED/
    PRESCRIPTION_ACCESSED, Phase 6), RAG search/answer generation (Phase
    9-14, including Admin AI - identified by a `structured_intent` value
    prefixed `ADMIN_`, not a separate event type), and - as of Phase 15 -
    permission denials (AUTHORIZATION_DENIED) and conversation-ownership
    denials (CONVERSATION_DENIED). LOGIN_SUCCESS/LOGIN_FAILURE are not
    written here, because the backend never observes those calls - the
    frontend talks to Supabase directly for sign-in (see
    docs/ARCHITECTURE.md, "Authentication events not yet audited").

    Phase 15: the insert runs inside its own SAVEPOINT
    (`session.begin_nested()` - the same pattern
    app/services/invitation_service.py already uses for an analogous
    "don't let one failure poison the whole transaction" need). A failure
    here is caught, logged as a safe operational warning (never the
    metadata content or the raw exception text, which could echo a bound
    parameter value), and swallowed - audit persistence is deliberately
    best-effort: an audit-write failure must never turn an otherwise-
    successful user request into a 500 (see
    docs/AUDIT_AND_OBSERVABILITY.md, "Audit transaction safety"). The
    caller's own subsequent `session.commit()` is unaffected either way,
    since the SAVEPOINT rollback (on failure) never poisons the outer
    transaction.
    """
    try:
        with session.begin_nested():
            session.execute(
                text(
                    """
                    INSERT INTO audit_logs
                        (actor_user_id, action, resource_type, resource_id, hospital_id, request_id, metadata)
                    VALUES
                        (:actor_user_id, :action, :resource_type, :resource_id, :hospital_id, :request_id, CAST(:metadata AS jsonb))
                    """
                ),
                {
                    "actor_user_id": str(actor_user_id) if actor_user_id else None,
                    "action": action,
                    "resource_type": resource_type,
                    "resource_id": str(resource_id) if resource_id else None,
                    "hospital_id": str(hospital_id) if hospital_id else None,
                    "request_id": str(request_id) if request_id else None,
                    "metadata": json.dumps(metadata or {}),
                },
            )
    except SQLAlchemyError:
        logger.warning("Audit event persistence failed for action=%s; continuing without it.", action)
