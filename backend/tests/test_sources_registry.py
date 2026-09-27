"""app/sources/{models,registry}.py: pure unit tests for the unified,
request-scoped source registry - no DB, no HTTP, no LLM dependency. See
docs/SOURCES_AND_CITATIONS.md, "Source registry" and "Source numbering".
"""

import uuid

from app.sources.models import STRUCTURED_SOURCE_TYPES, SourceType
from app.sources.registry import SourceRegistry

# --- sequential source IDs ---------------------------------------------


def test_sequential_numbering_starts_at_one():
    registry = SourceRegistry()
    a = registry.add(type=SourceType.APPOINTMENT, label="Appointment 1")
    b = registry.add(type=SourceType.APPOINTMENT, label="Appointment 2")
    assert a.number == 1
    assert b.number == 2


def test_id_field_derived_from_number():
    registry = SourceRegistry()
    source = registry.add(type=SourceType.DOCUMENT, label="Policy")
    assert source.id == "source-1"


def test_numbering_continues_across_mixed_types():
    registry = SourceRegistry()
    registry.add(type=SourceType.APPOINTMENT, label="Appointment")
    registry.add(type=SourceType.DOCUMENT, label="Policy")
    registry.add(type=SourceType.LAB_REPORT, label="Lab Report")
    assert [s.number for s in registry.sources] == [1, 2, 3]


# --- deterministic ordering ----------------------------------------------


def test_sources_preserve_insertion_order():
    registry = SourceRegistry()
    registry.add(type=SourceType.LAB_REPORT, label="third-inserted-conceptually")
    registry.add(type=SourceType.APPOINTMENT, label="second")
    registry.add(type=SourceType.DOCUMENT, label="first")
    assert [s.label for s in registry.sources] == ["third-inserted-conceptually", "second", "first"]


# --- duplicate prevention / no reuse of a number --------------------------


def test_numbers_are_never_reused_within_one_registry():
    registry = SourceRegistry()
    numbers = [registry.add(type=SourceType.DOCUMENT, label=f"doc {i}").number for i in range(5)]
    assert numbers == sorted(set(numbers))  # strictly increasing, no repeats


# --- request-scoped state --------------------------------------------------


def test_two_registries_never_share_state():
    """Simulates two separate answer-generation requests - each gets its
    own fresh registry, so numbering in one can never leak into or be
    influenced by the other (see docs/SOURCES_AND_CITATIONS.md, "Source
    registry" - "must be request-scoped")."""
    first = SourceRegistry()
    first.add(type=SourceType.DOCUMENT, label="Request A doc")

    second = SourceRegistry()  # a brand new request
    source = second.add(type=SourceType.DOCUMENT, label="Request B doc")

    assert source.number == 1  # not 2 - second registry started fresh
    assert len(first) == 1
    assert len(second) == 1


# --- is_valid() bounds checking -------------------------------------------


def test_is_valid_rejects_zero_negative_and_out_of_range():
    registry = SourceRegistry()
    registry.add(type=SourceType.DOCUMENT, label="only source")
    assert registry.is_valid(1) is True
    assert registry.is_valid(0) is False
    assert registry.is_valid(-1) is False
    assert registry.is_valid(2) is False
    assert registry.is_valid(999) is False


def test_is_valid_on_empty_registry_rejects_everything():
    registry = SourceRegistry()
    assert registry.is_valid(1) is False


# --- structured source registration ----------------------------------------


def test_structured_source_type_mapping_covers_every_implemented_source():
    """Every StructuredResult.source string
    (app/services/structured_query_service.py) has a real SourceType -
    matches Phase 11's seven-intent set plus Phase 14's five admin-AI
    source strings exactly, no invented types."""
    assert set(STRUCTURED_SOURCE_TYPES.keys()) == {
        "appointments",
        "medical_records",
        "lab_reports",
        "prescriptions",
        "doctors",
        "departments",
        "admin_employee_summary",
        "admin_pending_invitations",
        "admin_appointment_summary",
        "admin_document_summary",
        "admin_document_access_summary",
    }
    assert STRUCTURED_SOURCE_TYPES["appointments"] == SourceType.APPOINTMENT
    assert STRUCTURED_SOURCE_TYPES["medical_records"] == SourceType.MEDICAL_RECORD
    assert STRUCTURED_SOURCE_TYPES["lab_reports"] == SourceType.LAB_REPORT
    assert STRUCTURED_SOURCE_TYPES["prescriptions"] == SourceType.PRESCRIPTION
    assert STRUCTURED_SOURCE_TYPES["doctors"] == SourceType.DOCTOR
    assert STRUCTURED_SOURCE_TYPES["departments"] == SourceType.DEPARTMENT
    for admin_source in (
        "admin_employee_summary",
        "admin_pending_invitations",
        "admin_appointment_summary",
        "admin_document_summary",
        "admin_document_access_summary",
    ):
        assert STRUCTURED_SOURCE_TYPES[admin_source] == SourceType.ADMINISTRATIVE_SUMMARY


def test_structured_source_has_no_document_fields():
    registry = SourceRegistry()
    source = registry.add(type=SourceType.APPOINTMENT, label="Appointment — 2026-09-25 10:30")
    assert source.document_id is None
    assert source.document_title is None
    assert source.document_type is None
    assert source.chunk_id is None
    assert source.page is None
    assert source.section is None


# --- RAG source registration -----------------------------------------------


def test_rag_source_carries_document_fields():
    registry = SourceRegistry()
    doc_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    source = registry.add(
        type=SourceType.DOCUMENT,
        label="Cancellation Policy",
        document_id=doc_id,
        document_title="Cancellation Policy",
        document_type="HOSPITAL_POLICY",
        chunk_id=chunk_id,
        page=4,
        section="Cancellation",
    )
    assert source.document_id == doc_id
    assert source.chunk_id == chunk_id
    assert source.page == 4
    assert source.section == "Cancellation"


# --- no source types without a real implementation ------------------------


def test_no_patient_source_type_exists():
    """Phase 11 has no structured intent that returns patient-profile
    records directly (only records *about* a resolved patient) - so there
    is deliberately no PATIENT SourceType (see
    docs/SOURCES_AND_CITATIONS.md, "Unified source model")."""
    assert not hasattr(SourceType, "PATIENT")
    assert "patient" not in {member.value for member in SourceType}
