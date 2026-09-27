"""Phase 11 query-routing vocabulary. See docs/QUERY_ROUTING.md.

`StructuredIntent` is a strict, backend-owned allowlist - only intents
genuinely implementable against the EXISTING authorized data model
(app/repositories/clinical_repository.py) are listed here. Adding a value
here without also adding its handling in
app/services/structured_query_service.py is a bug, not a supported
extension point. Nothing in this module ever executes a query or makes an
authorization decision - it is pure vocabulary + the routing decision
shape.
"""

import dataclasses
from enum import StrEnum


class Route(StrEnum):
    RAG = "RAG"
    STRUCTURED = "STRUCTURED"
    HYBRID = "HYBRID"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"


class StructuredIntent(StrEnum):
    """Every value here must have a handler in
    app/services/structured_query_service.py's `_HANDLERS` - see that
    module's own consistency check."""

    MY_APPOINTMENTS = "MY_APPOINTMENTS"
    MY_MEDICAL_RECORDS = "MY_MEDICAL_RECORDS"
    MY_LAB_REPORTS = "MY_LAB_REPORTS"
    MY_PRESCRIPTIONS = "MY_PRESCRIPTIONS"
    PATIENT_APPOINTMENTS = "PATIENT_APPOINTMENTS"
    DOCTOR_DIRECTORY = "DOCTOR_DIRECTORY"
    DEPARTMENT_DIRECTORY = "DEPARTMENT_DIRECTORY"

    # Phase 14: read-only administrative intelligence, gated on admin-only
    # permissions (see app/services/structured_query_service.py's
    # _INTENT_PERMISSIONS) - never reachable by a non-admin role. See
    # docs/ADMIN_AI.md.
    ADMIN_EMPLOYEE_SUMMARY = "ADMIN_EMPLOYEE_SUMMARY"
    ADMIN_PENDING_INVITATIONS = "ADMIN_PENDING_INVITATIONS"
    ADMIN_APPOINTMENT_SUMMARY = "ADMIN_APPOINTMENT_SUMMARY"
    ADMIN_DOCUMENT_SUMMARY = "ADMIN_DOCUMENT_SUMMARY"
    ADMIN_DOCUMENT_ACCESS_SUMMARY = "ADMIN_DOCUMENT_ACCESS_SUMMARY"


@dataclasses.dataclass(frozen=True)
class QueryRoute:
    """The router's entire output - a classification, nothing more.
    `entity_reference` carries a raw natural-language fragment (e.g. a
    name mentioned in the query) that the structured query service must
    still resolve and independently authorize - it is never a database id
    and is never trusted as one. `classification_source` is internal
    diagnostics only ("deterministic" | "llm" | "fallback") - never
    exposed through the public API (see docs/QUERY_ROUTING.md, "Routing
    confidence").
    """

    route: Route
    structured_intent: StructuredIntent | None
    entity_reference: str | None
    classification_source: str
