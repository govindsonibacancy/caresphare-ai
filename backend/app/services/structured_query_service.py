"""Translates an approved `StructuredIntent` into authorized, minimized
structured data - see docs/QUERY_ROUTING.md, "Structured query flow".

This is NOT a second authorization system. Every intent's handler is a
thin wrapper around an EXISTING `clinical_repository.py` `list_*`
function - the exact same SQL-level, `UserScope`-derived authorization
Phase 6's own REST endpoints use - gated by the exact same `Permission`
values those endpoints require for the same underlying data (see
`_INTENT_PERMISSIONS` below, which only *composes* existing `Permission`
enum values, never invents a new check). No client-supplied id is ever
used for authorization: every id a handler uses either comes from
`UserScope` itself or from an already-scoped lookup
(`clinical_repository.find_patients_by_name`), never from the request.

A missing permission and a genuinely empty result both produce the
identical `NO_DATA` outcome - this generalizes the enumeration-protection
convention already established for documents/RAG (Phases 7-9) to
structured routing: nothing about the response ever reveals *why* there
was no data, only that there is none.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session

from app.permissions.authorization import has_permission
from app.permissions.permissions import Permission
from app.permissions.scope import UserScope
from app.repositories import admin_repository, clinical_repository
from app.repositories.clinical_repository import AppointmentRecord
from app.routing.intents import StructuredIntent
from app.services import admin_periods
from app.services.admin_periods import PeriodOutcome

# Bounded - every handler below caps its repository call at this limit.
# Never an unbounded/full-table query, never Python-side filtering of a
# broader fetch (see docs/QUERY_ROUTING.md, "Performance").
_STRUCTURED_QUERY_LIMIT = 10


class StructuredOutcome(StrEnum):
    OK = "OK"
    NO_DATA = "NO_DATA"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class StructuredResult:
    outcome: StructuredOutcome
    source: str
    records: list[dict]
    ambiguous_candidates: list[str] | None = None
    # Phase 14: an AMBIGUOUS result whose ambiguity isn't "which named
    # entity did you mean" (the candidates list) but something else - e.g.
    # an unresolvable date-range phrase - can supply its own complete,
    # ready-to-return clarification message here instead. When set,
    # app/llm/answer_service.py uses it verbatim rather than building the
    # generic "I found more than one match (...)" sentence from
    # `ambiguous_candidates`. None for every pre-Phase-14 caller, so
    # existing AMBIGUOUS behavior (entity-name ambiguity) is unchanged.
    ambiguous_message: str | None = None


def build_source_label(source: str, record: dict) -> str:
    """A safe, human-readable citation label for one structured record -
    see docs/SOURCES_AND_CITATIONS.md, "Structured sources". Built only
    from fields `structured_query_service.py`'s own handlers already put
    in the record dict (never a fresh lookup), and deliberately minimal:
    no raw foreign keys, no unrelated fields, just enough to say what the
    source *is*.
    """
    if source == "appointments":
        return f"Appointment — {record.get('date', '')} {record.get('time', '')}".strip()
    if source == "medical_records":
        return f"Medical Record — {record.get('title') or record.get('record_type', '')}"
    if source == "lab_reports":
        return f"Lab Report — {record.get('test_name', '')}"
    if source == "prescriptions":
        return f"Prescription — {record.get('medication_name', '')}"
    if source == "doctors":
        return f"Doctor — {record.get('name', '')}"
    if source == "departments":
        return f"Department — {record.get('name', '')}"
    if source == "admin_employee_summary":
        if "total_employees" in record:
            return f"Employee Summary — Total: {record['total_employees']}"
        return f"Employee Summary — {record.get('role', '')}: {record.get('count', '')}"
    if source == "admin_pending_invitations":
        if "pending_count" in record:
            return f"Pending Invitations — Total: {record['pending_count']}"
        return f"Pending Invitation — {record.get('email', '')}"
    if source == "admin_appointment_summary":
        if "total_appointments" in record:
            period = record.get("period_label")
            return f"Appointment Summary — Total: {record['total_appointments']}" + (f" — {period}" if period else "")
        return f"Appointment Summary — {record.get('department_name') or 'Unassigned'}: {record.get('count', '')}"
    if source == "admin_document_summary":
        if "total_documents" in record:
            return f"Document Summary — Total: {record['total_documents']}"
        return f"Document Summary — {record.get('status', '')}: {record.get('count', '')}"
    if source == "admin_document_access_summary":
        if "total_accessible" in record:
            return f"Document Access Summary — {record.get('role', '')} — Total: {record['total_accessible']}"
        return f"Document Access — {record.get('title', '')}"
    return source.replace("_", " ").title()


_NO_DATA = StructuredResult(outcome=StructuredOutcome.NO_DATA, source="", records=[])

# Mirrors each resource's existing LIST_PERMISSIONS exactly, as seen in
# app/api/{appointments,medical_records,lab_reports,prescriptions,doctors,
# departments}.py - composed here, not redefined.
_INTENT_PERMISSIONS: dict[StructuredIntent, tuple[Permission, ...]] = {
    StructuredIntent.MY_APPOINTMENTS: (
        Permission.VIEW_OWN_APPOINTMENTS,
        Permission.CREATE_APPOINTMENTS,
        Permission.MANAGE_APPOINTMENTS,
    ),
    StructuredIntent.MY_MEDICAL_RECORDS: (Permission.VIEW_PATIENT_MEDICAL_RECORDS,),
    StructuredIntent.MY_LAB_REPORTS: (Permission.VIEW_OWN_REPORTS, Permission.VIEW_PATIENT_MEDICAL_RECORDS),
    StructuredIntent.MY_PRESCRIPTIONS: (Permission.VIEW_OWN_PRESCRIPTIONS, Permission.VIEW_PATIENT_MEDICAL_RECORDS),
    StructuredIntent.PATIENT_APPOINTMENTS: (
        Permission.VIEW_OWN_APPOINTMENTS,
        Permission.CREATE_APPOINTMENTS,
        Permission.MANAGE_APPOINTMENTS,
    ),
    StructuredIntent.DOCTOR_DIRECTORY: (Permission.VIEW_HOSPITAL_OPERATIONS,),
    StructuredIntent.DEPARTMENT_DIRECTORY: (Permission.VIEW_HOSPITAL_OPERATIONS,),
    # Phase 14 - see docs/ADMIN_AI.md, "Permission matrix". Every one of
    # these three permissions is already seeded to HOSPITAL_ADMIN and
    # SUPER_ADMIN only (database/seeds/0001_demo_data.sql) - no new
    # permission was added for this phase.
    StructuredIntent.ADMIN_EMPLOYEE_SUMMARY: (Permission.MANAGE_USERS,),
    StructuredIntent.ADMIN_PENDING_INVITATIONS: (Permission.MANAGE_USERS,),
    StructuredIntent.ADMIN_APPOINTMENT_SUMMARY: (Permission.VIEW_HOSPITAL_ANALYTICS,),
    StructuredIntent.ADMIN_DOCUMENT_SUMMARY: (Permission.MANAGE_HOSPITAL_DOCUMENTS,),
    StructuredIntent.ADMIN_DOCUMENT_ACCESS_SUMMARY: (Permission.MANAGE_HOSPITAL_DOCUMENTS,),
}
# Matches app/api/patients.py's LIST_PERMISSIONS exactly - PATIENT_APPOINTMENTS'
# name-resolution step additionally requires this (see _patient_appointments).
_PATIENT_LOOKUP_PERMISSIONS = (Permission.VIEW_ASSIGNED_PATIENTS, Permission.VIEW_PATIENT_BASIC_INFORMATION)


def run_structured_query(
    session: Session, *, scope: UserScope, intent: StructuredIntent, entity_reference: str | None, query: str = ""
) -> StructuredResult:
    """The router decided `intent` - that is a classification, not an
    authorization decision (see docs/QUERY_ROUTING.md, "Routing is not
    authorization"). This function is where the real, independent
    permission check happens, exactly as it would for the equivalent REST
    endpoint.

    `query` (Phase 14, additive, default `""` so every pre-existing call
    site keeps working unchanged) is the raw current-turn question text -
    used only by the admin intents below, to deterministically resolve a
    date-period phrase (app/services/admin_periods.py) and an optional
    department-name filter. It is never trusted for anything beyond that:
    it does not widen authorization, and every admin handler still runs
    its own independent permission check exactly like every other intent.
    """
    if not has_permission(scope, _INTENT_PERMISSIONS[intent]):
        return _NO_DATA
    return _HANDLERS[intent](session, scope, entity_reference, query)


def is_intent_authorized(scope: UserScope, intent: StructuredIntent) -> bool:
    """Whether `scope` holds the permission `run_structured_query` would
    require for `intent` - exposed only so a caller (Phase 15's
    audit-labeling in app/llm/answer_service.py) can distinguish "denied"
    from "genuinely no data" for its own audit metadata. This is never a
    second, independent authorization decision - the actual gate remains
    entirely inside `run_structured_query` above, which performs the
    identical check itself before ever touching the database; calling
    this first (or not at all) can never widen or narrow what
    `run_structured_query` actually returns."""
    return has_permission(scope, _INTENT_PERMISSIONS[intent])


def _appointment_to_dict(session: Session, record: AppointmentRecord) -> dict:
    doctor = clinical_repository.get_doctor(session, record.doctor_id)
    doctor_name = clinical_repository.get_user_display_name(session, doctor.user_id) if doctor else None
    department = clinical_repository.get_department(session, record.department_id) if record.department_id else None
    return {
        "appointment_id": str(record.id),
        "date": record.appointment_date.isoformat(),
        "time": record.appointment_time.isoformat(),
        "status": record.status,
        "doctor": doctor_name,
        "department": department.name if department else None,
        "reason": record.reason,
    }


def _my_appointments(session: Session, scope: UserScope, _entity_reference: str | None, _query: str) -> StructuredResult:
    records, _total = clinical_repository.list_appointments(session, scope, limit=_STRUCTURED_QUERY_LIMIT, offset=0)
    if not records:
        return _NO_DATA
    return StructuredResult(
        outcome=StructuredOutcome.OK, source="appointments", records=[_appointment_to_dict(session, r) for r in records]
    )


def _my_medical_records(session: Session, scope: UserScope, _entity_reference: str | None, _query: str) -> StructuredResult:
    records, _total = clinical_repository.list_medical_records(session, scope, limit=_STRUCTURED_QUERY_LIMIT, offset=0)
    if not records:
        return _NO_DATA
    return StructuredResult(
        outcome=StructuredOutcome.OK,
        source="medical_records",
        records=[
            {
                "record_id": str(r.id),
                "record_type": r.record_type,
                "title": r.title,
                "description": r.description,
                "clinical_notes": r.clinical_notes,
                "recorded_at": r.recorded_at.isoformat(),
            }
            for r in records
        ],
    )


def _my_lab_reports(session: Session, scope: UserScope, _entity_reference: str | None, _query: str) -> StructuredResult:
    records, _total = clinical_repository.list_lab_reports(session, scope, limit=_STRUCTURED_QUERY_LIMIT, offset=0)
    if not records:
        return _NO_DATA
    return StructuredResult(
        outcome=StructuredOutcome.OK,
        source="lab_reports",
        records=[
            {
                "report_id": str(r.id),
                "test_name": r.test_name,
                "result": r.result,
                "unit": r.unit,
                "reference_range": r.reference_range,
                "status": r.status,
            }
            for r in records
        ],
    )


def _my_prescriptions(session: Session, scope: UserScope, _entity_reference: str | None, _query: str) -> StructuredResult:
    records, _total = clinical_repository.list_prescriptions(session, scope, limit=_STRUCTURED_QUERY_LIMIT, offset=0)
    if not records:
        return _NO_DATA
    return StructuredResult(
        outcome=StructuredOutcome.OK,
        source="prescriptions",
        records=[
            {
                "prescription_id": str(r.id),
                "medication_name": r.medication_name,
                "dosage": r.dosage,
                "frequency": r.frequency,
                "route": r.route,
                "duration": r.duration,
                "instructions": r.instructions,
            }
            for r in records
        ],
    )


def _patient_appointments(session: Session, scope: UserScope, entity_reference: str | None, _query: str) -> StructuredResult:
    """Resolves `entity_reference` (a raw name fragment from the query -
    never a database id) to a patient via `find_patients_by_name`, which
    is scoped identically to `list_patients` - so this can never reach a
    patient outside the caller's own authorization boundary, regardless of
    how many real patients share that name hospital-wide. Multiple matches
    -> AMBIGUOUS (candidate names only - all already within the caller's
    authorized boundary, so revealing them is not a leak). Zero matches ->
    the same NO_DATA every other "nothing found" case uses, never
    distinguishing "no such patient" from "exists but unauthorized" (see
    docs/QUERY_ROUTING.md, "Patient / resource references").
    """
    if not entity_reference or not has_permission(scope, _PATIENT_LOOKUP_PERMISSIONS):
        return _NO_DATA

    matches = clinical_repository.find_patients_by_name(session, scope, entity_reference)
    if not matches:
        return _NO_DATA
    if len(matches) > 1:
        return StructuredResult(
            outcome=StructuredOutcome.AMBIGUOUS,
            source="patients",
            records=[],
            ambiguous_candidates=[f"{m.first_name} {m.last_name}" for m in matches],
        )

    records, _total = clinical_repository.list_appointments(
        session, scope, limit=_STRUCTURED_QUERY_LIMIT, offset=0, patient_id=matches[0].patient_id
    )
    if not records:
        return _NO_DATA
    return StructuredResult(
        outcome=StructuredOutcome.OK, source="appointments", records=[_appointment_to_dict(session, r) for r in records]
    )


def _doctor_directory(session: Session, scope: UserScope, _entity_reference: str | None, _query: str) -> StructuredResult:
    records, _total = clinical_repository.list_doctors(session, scope, limit=_STRUCTURED_QUERY_LIMIT, offset=0)
    if not records:
        return _NO_DATA
    out = []
    for r in records:
        department = clinical_repository.get_department(session, r.department_id) if r.department_id else None
        out.append(
            {
                "doctor_id": str(r.id),
                "name": clinical_repository.get_user_display_name(session, r.user_id),
                "specialization": r.specialization,
                "department": department.name if department else None,
            }
        )
    return StructuredResult(outcome=StructuredOutcome.OK, source="doctors", records=out)


def _department_directory(session: Session, scope: UserScope, _entity_reference: str | None, _query: str) -> StructuredResult:
    records, _total = clinical_repository.list_hospital_departments(session, scope, limit=_STRUCTURED_QUERY_LIMIT, offset=0)
    if not records:
        return _NO_DATA
    return StructuredResult(
        outcome=StructuredOutcome.OK,
        source="departments",
        records=[{"department_id": str(r.id), "name": r.name, "code": r.code, "description": r.description} for r in records],
    )


# --- Phase 14: admin AI handlers ---------------------------------------
#
# Every handler below follows the same shape: a backend-computed TOTAL
# record first (so the LLM always has an authoritative number and never
# has to count list items itself - see docs/ADMIN_AI.md, "Aggregation
# correctness"), then a bounded breakdown. Every query is read-only and
# reuses admin_repository.py's own hospital-scoping (SUPER_ADMIN's
# existing unscoped bypass), never a client-supplied hospital/id of any
# kind - `entity_reference`/`query` are raw natural-language text, never
# trusted as an id.

_ROLE_NAME_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bsuper\s*admins?\b", re.IGNORECASE), "SUPER_ADMIN"),
    (re.compile(r"\bhospital\s*admins?\b", re.IGNORECASE), "HOSPITAL_ADMIN"),
    (re.compile(r"\bdoctors?\b", re.IGNORECASE), "DOCTOR"),
    (re.compile(r"\bnurses?\b", re.IGNORECASE), "NURSE"),
    (re.compile(r"\breceptionists?\b", re.IGNORECASE), "RECEPTIONIST"),
    (re.compile(r"\bstaff\b", re.IGNORECASE), "STAFF"),
]


def _extract_role_name(query: str) -> str | None:
    """Deterministic, backend-owned role-word recognition - never an LLM
    guess. Checked in most-specific-first order so "hospital admin" isn't
    swallowed by a hypothetical broader pattern. Returns None (not a
    guess) when no recognized role word appears."""
    for pattern, role_name in _ROLE_NAME_PATTERNS:
        if pattern.search(query):
            return role_name
    return None


def _admin_employee_summary(session: Session, scope: UserScope, _entity_reference: str | None, _query: str) -> StructuredResult:
    by_role = admin_repository.count_employees_by_role(session, scope)
    total = sum(r.count for r in by_role)
    records: list[dict] = [{"total_employees": total}]
    records.extend({"role": r.role, "count": r.count} for r in by_role)
    return StructuredResult(outcome=StructuredOutcome.OK, source="admin_employee_summary", records=records)


def _admin_pending_invitations(session: Session, scope: UserScope, _entity_reference: str | None, _query: str) -> StructuredResult:
    total = admin_repository.count_pending_invitations(session, scope)
    records: list[dict] = [{"pending_count": total}]
    recent = admin_repository.list_recent_pending_invitations(session, scope)
    records.extend(
        {
            "email": r.email,
            "first_name": r.first_name,
            "last_name": r.last_name,
            "role": r.role,
            "created_at": r.created_at.isoformat(),
        }
        for r in recent
    )
    return StructuredResult(outcome=StructuredOutcome.OK, source="admin_pending_invitations", records=records)


def _admin_appointment_summary(session: Session, scope: UserScope, _entity_reference: str | None, query: str) -> StructuredResult:
    period = admin_periods.resolve_period(query)
    if period.outcome == PeriodOutcome.AMBIGUOUS:
        return StructuredResult(
            outcome=StructuredOutcome.AMBIGUOUS,
            source="admin_appointment_summary",
            records=[],
            ambiguous_message=(
                "I can't tell exactly which time period you mean. Could you ask about a specific one - "
                "for example today, this week, this month, last month, this year, or last year?"
            ),
        )
    date_from = period.start if period.outcome == PeriodOutcome.RESOLVED else None
    date_to = period.end if period.outcome == PeriodOutcome.RESOLVED else None

    # Department resolution reads the raw query text directly (see
    # admin_repository.find_department_by_name) rather than
    # `entity_reference` - it works the same way whether Layer 1 or
    # Layer 2 classified this query, and Layer 2 never populates
    # entity_reference at all (see docs/QUERY_ROUTING.md).
    department_id = admin_repository.find_department_by_name(session, scope, query)

    total = admin_repository.count_appointments_total(session, scope, department_id=department_id, date_from=date_from, date_to=date_to)
    by_department = admin_repository.count_appointments_by_department(
        session, scope, department_id=department_id, date_from=date_from, date_to=date_to
    )
    records: list[dict] = [
        {"total_appointments": total, **({"period_label": period.label} if period.label else {})}
    ]
    records.extend(
        {"department_name": r.department_name, "count": r.count} for r in by_department
    )
    # Zero is a real, authorized answer (docs/ADMIN_AI.md, "Empty results")
    # - never converted to the generic no-context/NO_DATA path, which
    # would make a genuine "0 appointments this month" indistinguishable
    # from "you aren't authorized to see this."
    return StructuredResult(outcome=StructuredOutcome.OK, source="admin_appointment_summary", records=records)


def _admin_document_summary(session: Session, scope: UserScope, _entity_reference: str | None, _query: str) -> StructuredResult:
    total = admin_repository.count_documents_total(session, scope)
    by_status = admin_repository.count_documents_by_status(session, scope)
    records: list[dict] = [{"total_documents": total}]
    records.extend({"status": r.status, "count": r.count} for r in by_status)
    return StructuredResult(outcome=StructuredOutcome.OK, source="admin_document_summary", records=records)


def _admin_document_access_summary(session: Session, scope: UserScope, entity_reference: str | None, query: str) -> StructuredResult:
    role_name = _extract_role_name(entity_reference or query)
    if role_name is None:
        return _NO_DATA
    total = admin_repository.count_documents_accessible_by_role(session, scope, role_name)
    accessible = admin_repository.list_documents_accessible_by_role(session, scope, role_name)
    records: list[dict] = [{"role": role_name, "total_accessible": total}]
    records.extend({"title": r.title, "document_type": r.document_type} for r in accessible)
    # Zero is a real answer here too (see _admin_appointment_summary) -
    # NO_DATA is reserved for "the role couldn't be determined at all"
    # above, never for "the role was recognized but has no access."
    return StructuredResult(outcome=StructuredOutcome.OK, source="admin_document_access_summary", records=records)


_HANDLERS = {
    StructuredIntent.MY_APPOINTMENTS: _my_appointments,
    StructuredIntent.MY_MEDICAL_RECORDS: _my_medical_records,
    StructuredIntent.MY_LAB_REPORTS: _my_lab_reports,
    StructuredIntent.MY_PRESCRIPTIONS: _my_prescriptions,
    StructuredIntent.PATIENT_APPOINTMENTS: _patient_appointments,
    StructuredIntent.DOCTOR_DIRECTORY: _doctor_directory,
    StructuredIntent.DEPARTMENT_DIRECTORY: _department_directory,
    StructuredIntent.ADMIN_EMPLOYEE_SUMMARY: _admin_employee_summary,
    StructuredIntent.ADMIN_PENDING_INVITATIONS: _admin_pending_invitations,
    StructuredIntent.ADMIN_APPOINTMENT_SUMMARY: _admin_appointment_summary,
    StructuredIntent.ADMIN_DOCUMENT_SUMMARY: _admin_document_summary,
    StructuredIntent.ADMIN_DOCUMENT_ACCESS_SUMMARY: _admin_document_access_summary,
}

assert set(_HANDLERS) == set(StructuredIntent), "structured_query_service is missing a handler for a StructuredIntent"
