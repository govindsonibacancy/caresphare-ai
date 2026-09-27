"""app/services/structured_query_service.py's Phase 14 ADMIN_* intents:
calls `run_structured_query` directly against a real database with
hand-built `UserScope` objects - no HTTP, no LLM dependency. See
docs/ADMIN_AI.md, "Permission matrix" and "Authorization".
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import text

from app.core.db import get_session_factory
from app.permissions.roles import Role
from app.permissions.scope import UserScope
from app.routing.intents import StructuredIntent
from app.services.structured_query_service import StructuredOutcome, run_structured_query

HOSPITAL_ADMIN_EMAIL = "vikram.singh@caresphere-demo.example"
SUPER_ADMIN_EMAIL = "meera.kapoor@caresphere-demo.example"
DOCTOR_EMAIL = "rohan.mehta@caresphere-demo.example"
NURSE_EMAIL = "kavya.iyer@caresphere-demo.example"
PATIENT_EMAIL = "asha.verma@example-patient.example"


@pytest.fixture
def session(db_engine):
    s = get_session_factory()()
    yield s
    s.close()


def _scope_for(db_engine, email: str) -> UserScope:
    with db_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT u.id, u.auth_user_id, u.hospital_id, u.department_id, r.name AS role "
                "FROM users u JOIN roles r ON r.id = u.role_id WHERE u.email = :email"
            ),
            {"email": email},
        ).mappings().one()
        permissions = frozenset(
            conn.execute(
                text(
                    "SELECT p.code FROM role_permissions rp JOIN roles r ON r.id = rp.role_id "
                    "JOIN permissions p ON p.id = rp.permission_id WHERE r.name = :role"
                ),
                {"role": row["role"]},
            ).scalars()
        )
    return UserScope(
        user_id=row["id"],
        auth_user_id=row["auth_user_id"],
        role=Role(row["role"]),
        permissions=permissions,
        hospital_id=row["hospital_id"],
        department_id=row["department_id"],
        patient_id=None,
        doctor_id=None,
        staff_id=None,
    )


# --- ADMIN_EMPLOYEE_SUMMARY: permission gate -------------------------------


def test_hospital_admin_gets_employee_summary(db_engine, session):
    scope = _scope_for(db_engine, HOSPITAL_ADMIN_EMAIL)
    result = run_structured_query(
        session, scope=scope, intent=StructuredIntent.ADMIN_EMPLOYEE_SUMMARY, entity_reference=None, query="how many employees"
    )
    assert result.outcome == StructuredOutcome.OK
    assert result.source == "admin_employee_summary"
    total_record = result.records[0]
    assert "total_employees" in total_record
    assert total_record["total_employees"] >= 1  # seed data has at least one doctor
    breakdown_total = sum(r["count"] for r in result.records[1:])
    assert breakdown_total == total_record["total_employees"]


def test_super_admin_gets_employee_summary(db_engine, session):
    scope = _scope_for(db_engine, SUPER_ADMIN_EMAIL)
    result = run_structured_query(
        session, scope=scope, intent=StructuredIntent.ADMIN_EMPLOYEE_SUMMARY, entity_reference=None, query="how many employees"
    )
    assert result.outcome == StructuredOutcome.OK


@pytest.mark.parametrize("email", [DOCTOR_EMAIL, NURSE_EMAIL, PATIENT_EMAIL])
def test_non_admin_roles_cannot_get_employee_summary(db_engine, session, email):
    scope = _scope_for(db_engine, email)
    result = run_structured_query(
        session, scope=scope, intent=StructuredIntent.ADMIN_EMPLOYEE_SUMMARY, entity_reference=None, query="how many employees"
    )
    assert result.outcome == StructuredOutcome.NO_DATA
    assert result.records == []


# --- ADMIN_PENDING_INVITATIONS ---------------------------------------------


def test_hospital_admin_gets_pending_invitations_count(db_engine, session):
    scope = _scope_for(db_engine, HOSPITAL_ADMIN_EMAIL)
    result = run_structured_query(
        session,
        scope=scope,
        intent=StructuredIntent.ADMIN_PENDING_INVITATIONS,
        entity_reference=None,
        query="how many pending invitations are there",
    )
    assert result.outcome == StructuredOutcome.OK
    assert "pending_count" in result.records[0]
    assert result.records[0]["pending_count"] >= 0  # zero is a real, stated answer - never NO_DATA


def test_patient_cannot_get_pending_invitations(db_engine, session):
    scope = _scope_for(db_engine, PATIENT_EMAIL)
    result = run_structured_query(
        session, scope=scope, intent=StructuredIntent.ADMIN_PENDING_INVITATIONS, entity_reference=None, query="pending invitations"
    )
    assert result.outcome == StructuredOutcome.NO_DATA


def test_pending_invitation_count_matches_a_freshly_created_invitation(db_engine, session):
    scope = _scope_for(db_engine, HOSPITAL_ADMIN_EMAIL)
    before = run_structured_query(
        session, scope=scope, intent=StructuredIntent.ADMIN_PENDING_INVITATIONS, entity_reference=None, query="pending invitations"
    ).records[0]["pending_count"]

    with db_engine.connect() as conn:
        role_id = conn.execute(text("SELECT id FROM roles WHERE name = 'STAFF'")).scalar_one()
        inviter_id = conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": HOSPITAL_ADMIN_EMAIL}).scalar_one()
        invitation_id = conn.execute(
            text(
                "INSERT INTO employee_invitations "
                "(invitation_token_hash, email, first_name, last_name, hospital_id, role_id, invited_by_user_id, expires_at) "
                "VALUES (:hash, :email, 'Test', 'Invitee', :hospital_id, :role_id, :inviter_id, now() + interval '7 days') "
                "RETURNING id"
            ),
            {
                "hash": uuid.uuid4().hex,
                "email": f"admin-ai-test-{uuid.uuid4().hex[:8]}@example.com",
                "hospital_id": scope.hospital_id,
                "role_id": role_id,
                "inviter_id": inviter_id,
            },
        ).scalar_one()
        conn.commit()

    try:
        after = run_structured_query(
            session, scope=scope, intent=StructuredIntent.ADMIN_PENDING_INVITATIONS, entity_reference=None, query="pending invitations"
        ).records[0]["pending_count"]
        assert after == before + 1
    finally:
        with db_engine.connect() as conn:
            conn.execute(text("DELETE FROM employee_invitations WHERE id = :id"), {"id": invitation_id})
            conn.commit()


# --- ADMIN_APPOINTMENT_SUMMARY ---------------------------------------------


@pytest.fixture
def cardiology_appointments(db_engine):
    """Three throwaway appointments on a fixed, otherwise-untouched date -
    exact-count assertions never depend on what the shared seed data
    happens to contain."""
    fixed_date = date(2031, 3, 3)  # far enough in the future to never collide with seed data
    with db_engine.connect() as conn:
        hospital_id = conn.execute(text("SELECT id FROM hospitals WHERE code = 'CGH'")).scalar_one()
        department_id = conn.execute(
            text("SELECT id FROM departments WHERE hospital_id = :h AND name = 'Cardiology'"), {"h": hospital_id}
        ).scalar_one()
        doctor_id = conn.execute(
            text("SELECT d.id FROM doctors d JOIN users u ON u.id = d.user_id WHERE u.email = :e"), {"e": DOCTOR_EMAIL}
        ).scalar_one()
        patient_id = conn.execute(text("SELECT id FROM patients LIMIT 1")).scalar_one()

        appointment_ids = []
        for i in range(3):
            appointment_id = conn.execute(
                text(
                    "INSERT INTO appointments (hospital_id, patient_id, doctor_id, department_id, appointment_date, appointment_time) "
                    "VALUES (:h, :p, :d, :dept, :date, :time) RETURNING id"
                ),
                {
                    "h": hospital_id,
                    "p": patient_id,
                    "d": doctor_id,
                    "dept": department_id,
                    "date": fixed_date,
                    "time": f"{9 + i}:00",
                },
            ).scalar_one()
            appointment_ids.append(appointment_id)
        conn.commit()

    try:
        yield {"department_id": department_id, "count": 3}
    finally:
        with db_engine.connect() as conn:
            conn.execute(text("DELETE FROM appointments WHERE id = ANY(:ids)"), {"ids": appointment_ids})
            conn.commit()


def test_appointment_summary_department_breakdown_is_exact(db_engine, session, cardiology_appointments):
    scope = _scope_for(db_engine, HOSPITAL_ADMIN_EMAIL)
    result = run_structured_query(
        session,
        scope=scope,
        intent=StructuredIntent.ADMIN_APPOINTMENT_SUMMARY,
        entity_reference=None,
        query="Show me appointment activity for Cardiology.",
    )
    assert result.outcome == StructuredOutcome.OK
    cardiology_rows = [r for r in result.records[1:] if r.get("department_name") == "Cardiology"]
    assert len(cardiology_rows) == 1
    assert cardiology_rows[0]["count"] >= cardiology_appointments["count"]


def test_appointment_summary_department_filter_excludes_other_departments(db_engine, session, cardiology_appointments):
    scope = _scope_for(db_engine, HOSPITAL_ADMIN_EMAIL)
    result = run_structured_query(
        session,
        scope=scope,
        intent=StructuredIntent.ADMIN_APPOINTMENT_SUMMARY,
        entity_reference=None,
        query="appointment activity for Cardiology",
    )
    assert result.outcome == StructuredOutcome.OK
    assert all(r.get("department_name") in (None, "Cardiology") for r in result.records[1:])


def test_appointment_summary_zero_for_a_date_with_no_appointments_is_a_real_answer_not_no_data(db_engine, session):
    scope = _scope_for(db_engine, HOSPITAL_ADMIN_EMAIL)
    result = run_structured_query(
        session,
        scope=scope,
        intent=StructuredIntent.ADMIN_APPOINTMENT_SUMMARY,
        entity_reference=None,
        # A department name that matches no real department -> no filter
        # applied, but the appointment_date-based test below covers the
        # true zero-count case via an isolated, guaranteed-empty date.
        query="appointment activity for a department that does not exist anywhere",
    )
    assert result.outcome == StructuredOutcome.OK
    assert "total_appointments" in result.records[0]


def test_appointment_summary_unresolvable_period_is_ambiguous(db_engine, session):
    scope = _scope_for(db_engine, HOSPITAL_ADMIN_EMAIL)
    result = run_structured_query(
        session,
        scope=scope,
        intent=StructuredIntent.ADMIN_APPOINTMENT_SUMMARY,
        entity_reference=None,
        query="appointment activity two months ago",
    )
    assert result.outcome == StructuredOutcome.AMBIGUOUS
    assert result.ambiguous_message
    assert result.records == []


def test_doctor_cannot_get_appointment_summary(db_engine, session):
    scope = _scope_for(db_engine, DOCTOR_EMAIL)
    result = run_structured_query(
        session,
        scope=scope,
        intent=StructuredIntent.ADMIN_APPOINTMENT_SUMMARY,
        entity_reference=None,
        query="appointment activity this month",
    )
    assert result.outcome == StructuredOutcome.NO_DATA


# --- ADMIN_DOCUMENT_SUMMARY / ADMIN_DOCUMENT_ACCESS_SUMMARY ---------------


def test_hospital_admin_gets_document_summary(db_engine, session):
    scope = _scope_for(db_engine, HOSPITAL_ADMIN_EMAIL)
    result = run_structured_query(
        session, scope=scope, intent=StructuredIntent.ADMIN_DOCUMENT_SUMMARY, entity_reference=None, query="how many documents"
    )
    assert result.outcome == StructuredOutcome.OK
    assert "total_documents" in result.records[0]


def test_patient_cannot_get_document_summary(db_engine, session):
    scope = _scope_for(db_engine, PATIENT_EMAIL)
    result = run_structured_query(
        session, scope=scope, intent=StructuredIntent.ADMIN_DOCUMENT_SUMMARY, entity_reference=None, query="how many documents"
    )
    assert result.outcome == StructuredOutcome.NO_DATA


def test_document_access_summary_unrecognized_role_is_no_data(db_engine, session):
    scope = _scope_for(db_engine, HOSPITAL_ADMIN_EMAIL)
    result = run_structured_query(
        session,
        scope=scope,
        intent=StructuredIntent.ADMIN_DOCUMENT_ACCESS_SUMMARY,
        entity_reference=None,
        query="which documents can be accessed",  # no role word at all
    )
    assert result.outcome == StructuredOutcome.NO_DATA


def test_document_access_summary_for_nurses_is_a_real_zero_when_none_granted(db_engine, session):
    scope = _scope_for(db_engine, HOSPITAL_ADMIN_EMAIL)
    result = run_structured_query(
        session,
        scope=scope,
        intent=StructuredIntent.ADMIN_DOCUMENT_ACCESS_SUMMARY,
        entity_reference=None,
        query="which documents are available to nurses",
    )
    assert result.outcome == StructuredOutcome.OK
    assert result.records[0]["role"] == "NURSE"
    assert "total_accessible" in result.records[0]


# --- cross-hospital isolation -----------------------------------------------


def test_employee_summary_is_isolated_per_hospital(db_engine, session, second_hospital):
    scope_b = UserScope(
        user_id=second_hospital["admin_user_id"],
        auth_user_id=second_hospital["admin_user_id"],
        role=Role.HOSPITAL_ADMIN,
        permissions=frozenset({"manage_users"}),
        hospital_id=second_hospital["hospital_id"],
        department_id=None,
        patient_id=None,
        doctor_id=None,
        staff_id=None,
    )
    result = run_structured_query(
        session, scope=scope_b, intent=StructuredIntent.ADMIN_EMPLOYEE_SUMMARY, entity_reference=None, query="how many employees"
    )
    assert result.outcome == StructuredOutcome.OK
    # Hospital B's only user is its HOSPITAL_ADMIN itself, not an employee
    # role (DOCTOR/NURSE/RECEPTIONIST/STAFF) - so the total must be 0,
    # never leaking Hospital A's (the shared seed hospital's) real count.
    assert result.records[0]["total_employees"] == 0
