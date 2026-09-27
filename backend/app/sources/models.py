"""The unified source model - one representation for both RAG document
chunks (Phase 9) and structured records (Phase 11). See
docs/SOURCES_AND_CITATIONS.md, "Unified source model".

`SourceType` is a closed enum matching exactly the real, implemented
retrieval capabilities - `DOCUMENT` for RAG chunks, and one value per
`app/services/structured_query_service.py` source string
("appointments" -> APPOINTMENT, etc.). No value here has any
corresponding data unless one of those two pipelines can genuinely
produce it - there is no `PATIENT` type, for example, because no
structured intent returns patient-profile records (only records *about* a
resolved patient, e.g. their appointments).
"""

import uuid
from dataclasses import dataclass
from enum import StrEnum


class SourceType(StrEnum):
    DOCUMENT = "document"
    APPOINTMENT = "appointment"
    MEDICAL_RECORD = "medical_record"
    LAB_REPORT = "lab_report"
    PRESCRIPTION = "prescription"
    DOCTOR = "doctor"
    DEPARTMENT = "department"
    # Phase 14: one shared type for every admin-AI aggregate/summary
    # source (employee/invitation/appointment/document counts and
    # breakdowns) - see docs/ADMIN_AI.md, "Source/citation integration".
    # A single type rather than one per admin intent: all five are the
    # same *kind* of thing (a backend-computed administrative summary, no
    # per-record foreign key), unlike APPOINTMENT/DOCTOR/etc. which each
    # correspond to a genuinely different clinical/directory resource.
    ADMINISTRATIVE_SUMMARY = "administrative_summary"


# Maps app/services/structured_query_service.py's StructuredResult.source
# strings to SourceType - the one place that mapping is defined.
STRUCTURED_SOURCE_TYPES: dict[str, SourceType] = {
    "appointments": SourceType.APPOINTMENT,
    "medical_records": SourceType.MEDICAL_RECORD,
    "lab_reports": SourceType.LAB_REPORT,
    "prescriptions": SourceType.PRESCRIPTION,
    "doctors": SourceType.DOCTOR,
    "departments": SourceType.DEPARTMENT,
    "admin_employee_summary": SourceType.ADMINISTRATIVE_SUMMARY,
    "admin_pending_invitations": SourceType.ADMINISTRATIVE_SUMMARY,
    "admin_appointment_summary": SourceType.ADMINISTRATIVE_SUMMARY,
    "admin_document_summary": SourceType.ADMINISTRATIVE_SUMMARY,
    "admin_document_access_summary": SourceType.ADMINISTRATIVE_SUMMARY,
}


@dataclass(frozen=True)
class Source:
    """One backend-authoritative citation entry. `number` is the
    "[Source N]" value the model is shown and may cite; `id` is a stable,
    non-guessable-by-sequence-alone string form of it for API consumers
    (`"source-{number}"`) - both are assigned by `SourceRegistry`, never by
    the model or the client.

    Document-only fields (`document_id`/`document_title`/`document_type`/
    `chunk_id`/`page`/`section`) are `None` for a structured source; there
    is no `record_id`/foreign-key field for structured sources at all -
    see docs/SOURCES_AND_CITATIONS.md, "Structured sources" for why one
    isn't needed (the `label` is already the safe, backend-built citation
    text).
    """

    number: int
    id: str
    type: SourceType
    label: str
    document_id: uuid.UUID | None = None
    document_title: str | None = None
    document_type: str | None = None
    chunk_id: uuid.UUID | None = None
    page: int | None = None
    section: str | None = None
