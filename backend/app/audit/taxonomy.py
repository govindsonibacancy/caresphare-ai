"""Shared, closed vocabulary for audit event metadata - see
docs/AUDIT_AND_OBSERVABILITY.md, "Event taxonomy" and "Error
classification". A fixed, small enum rather than free-form strings, so
metadata stays comparable/queryable across call sites instead of each one
inventing its own spelling of "denied" or "failed". Audit metadata is not
application data (see docs/AUDIT_AND_OBSERVABILITY.md, "Core principle") -
neither enum here is ever built from, or contains, request/response
content.
"""

from enum import StrEnum


class AuditResult(StrEnum):
    """The outcome of one audited AI-pipeline operation. Deliberately
    coarse - never distinguishes *which* permission or *which* resource
    was involved beyond this fixed vocabulary (see
    docs/AUDIT_AND_OBSERVABILITY.md, "Authorization denial must not leak
    information") - that detail, where safe, lives in a separate,
    allowlisted metadata field (e.g. `permission`), never inferred from a
    result string."""

    SUCCESS = "SUCCESS"
    DENIED = "DENIED"
    NO_DATA = "NO_DATA"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"
    FAILED = "FAILED"


class ErrorCode(StrEnum):
    """Safe, closed failure categories - never a raw exception message,
    which could echo SQL, a connection string, or a bound parameter value.
    Only categories this codebase can actually produce are listed; see
    docs/AUDIT_AND_OBSERVABILITY.md, "Error classification"."""

    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS_QUERY = "AMBIGUOUS_QUERY"
    RETRIEVAL_FAILURE = "RETRIEVAL_FAILURE"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    LLM_TIMEOUT = "LLM_TIMEOUT"
