"""Data access for Phase 14 admin-AI aggregates - see docs/ADMIN_AI.md.

A separate file rather than additions to clinical_repository.py/
document_repository.py/invitation_repository.py: every query here is
shaped specifically for a bounded, LLM-facing administrative summary
(a small total + a bounded breakdown), not the general-purpose CRUD/list
surface those files already provide for their own REST endpoints - mixing
the two would blur each file's existing, focused purpose. Every function
here is still read-only and reuses the same hospital-scoping rule Phase
11's structured intents already established (`_admin_hospital_scope_clause`
mirrors clinical_repository.py's own `_hospital_scope_clause` exactly -
SUPER_ADMIN gets the same unscoped/cross-hospital bypass Phase 11's
DOCTOR_DIRECTORY/DEPARTMENT_DIRECTORY intents already have, not a new
rule). None of these functions do their own permission check - that is
`structured_query_service._INTENT_PERMISSIONS`'s job, exactly as it is
for every other structured intent.
"""

import datetime
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.permissions.roles import Role
from app.permissions.scope import UserScope

# Every admin list-shaped result is capped at this bound, matching
# structured_query_service.py's own _STRUCTURED_QUERY_LIMIT - never an
# unbounded fetch, never Python-side slicing of a broader query.
ADMIN_LIST_LIMIT = 10

_EMPLOYEE_ROLES = ("DOCTOR", "NURSE", "RECEPTIONIST", "STAFF")


def _admin_hospital_scope_clause(scope: UserScope, *, column: str = "hospital_id") -> tuple[str, dict]:
    if scope.role is Role.SUPER_ADMIN:
        return "TRUE", {}
    return f"{column} = :scope_hospital_id", {"scope_hospital_id": scope.hospital_id}


@dataclass(frozen=True)
class RoleCount:
    role: str
    count: int


def count_employees_by_role(session: Session, scope: UserScope) -> list[RoleCount]:
    where_sql, params = _admin_hospital_scope_clause(scope, column="u.hospital_id")
    rows = session.execute(
        text(
            f"""
            SELECT r.name AS role, count(*) AS count
            FROM users u
            JOIN roles r ON r.id = u.role_id
            WHERE {where_sql} AND u.is_active AND r.name = ANY(:employee_roles)
            GROUP BY r.name
            ORDER BY count DESC, r.name ASC
            """
        ),
        {**params, "employee_roles": list(_EMPLOYEE_ROLES)},
    ).mappings()
    return [RoleCount(role=row["role"], count=row["count"]) for row in rows]


def count_pending_invitations(session: Session, scope: UserScope) -> int:
    where_sql, params = _admin_hospital_scope_clause(scope)
    return session.execute(
        text(f"SELECT count(*) FROM employee_invitations WHERE {where_sql} AND status = 'PENDING'"), params
    ).scalar_one()


@dataclass(frozen=True)
class PendingInvitationSummary:
    email: str
    first_name: str
    last_name: str
    role: str
    department_id: uuid.UUID | None
    created_at: datetime.datetime


def list_recent_pending_invitations(session: Session, scope: UserScope, *, limit: int = ADMIN_LIST_LIMIT) -> list[PendingInvitationSummary]:
    where_sql, params = _admin_hospital_scope_clause(scope, column="ei.hospital_id")
    rows = session.execute(
        text(
            f"""
            SELECT ei.email, ei.first_name, ei.last_name, r.name AS role, ei.department_id, ei.created_at
            FROM employee_invitations ei
            JOIN roles r ON r.id = ei.role_id
            WHERE {where_sql} AND ei.status = 'PENDING'
            ORDER BY ei.created_at DESC
            LIMIT :limit
            """
        ),
        {**params, "limit": limit},
    ).mappings()
    return [PendingInvitationSummary(**row) for row in rows]


@dataclass(frozen=True)
class DepartmentAppointmentCount:
    department_id: uuid.UUID | None
    department_name: str | None
    count: int


def count_appointments_total(
    session: Session,
    scope: UserScope,
    *,
    department_id: uuid.UUID | None,
    date_from: datetime.date | None,
    date_to: datetime.date | None,
) -> int:
    where_sql, params = _admin_hospital_scope_clause(scope)
    filters, params = _appointment_summary_filters(department_id, date_from, date_to, params)
    where_sql = " AND ".join([where_sql, *filters]) if filters else where_sql
    return session.execute(text(f"SELECT count(*) FROM appointments WHERE {where_sql}"), params).scalar_one()


def count_appointments_by_department(
    session: Session,
    scope: UserScope,
    *,
    department_id: uuid.UUID | None,
    date_from: datetime.date | None,
    date_to: datetime.date | None,
    limit: int = 50,
) -> list[DepartmentAppointmentCount]:
    """Bounded to `limit` departments (default 50, well above any realistic
    department count for a single demo hospital) - never an unbounded
    GROUP BY. SUPER_ADMIN's unscoped view can span several hospitals'
    departments, which is why this isn't capped at ADMIN_LIST_LIMIT like a
    single-hospital directory would be."""
    where_sql, params = _admin_hospital_scope_clause(scope, column="a.hospital_id")
    filters, params = _appointment_summary_filters(department_id, date_from, date_to, params, column_prefix="a.")
    where_sql = " AND ".join([where_sql, *filters]) if filters else where_sql
    rows = session.execute(
        text(
            f"""
            SELECT a.department_id, d.name AS department_name, count(*) AS count
            FROM appointments a
            LEFT JOIN departments d ON d.id = a.department_id
            WHERE {where_sql}
            GROUP BY a.department_id, d.name
            ORDER BY count DESC, department_name ASC NULLS LAST
            LIMIT :limit
            """
        ),
        {**params, "limit": limit},
    ).mappings()
    return [DepartmentAppointmentCount(**row) for row in rows]


def _appointment_summary_filters(
    department_id: uuid.UUID | None,
    date_from: datetime.date | None,
    date_to: datetime.date | None,
    params: dict,
    *,
    column_prefix: str = "",
) -> tuple[list[str], dict]:
    filters = []
    params = dict(params)
    if department_id is not None:
        filters.append(f"{column_prefix}department_id = :filter_department_id")
        params["filter_department_id"] = department_id
    if date_from is not None:
        filters.append(f"{column_prefix}appointment_date >= :filter_date_from")
        params["filter_date_from"] = date_from
    if date_to is not None:
        filters.append(f"{column_prefix}appointment_date <= :filter_date_to")
        params["filter_date_to"] = date_to
    return filters, params


def find_department_by_name(session: Session, scope: UserScope, query_text: str) -> uuid.UUID | None:
    """Resolves a department mentioned by name somewhere in the caller's
    raw question text (e.g. "...appointment activity for Cardiology")
    to a single department id, scoped identically to
    `count_appointments_by_department`. Deliberately the reverse of a
    typical name search: rather than extracting a name fragment from the
    query first (which would need its own regex, duplicated between
    Layer 1 and Layer 2 classification), this checks - for each
    already-authorized department - whether *its* name appears as a
    substring of the query text. Works identically regardless of which
    routing layer classified the query. Returns None for zero or
    multiple matches (an appointment summary with an unresolved
    department name simply falls back to the full, unfiltered breakdown
    - see docs/ADMIN_AI.md, "Reference resolution").
    """
    where_sql, params = _admin_hospital_scope_clause(scope)
    rows = (
        session.execute(
            text(f"SELECT id FROM departments WHERE {where_sql} AND :query_text ILIKE '%' || name || '%' LIMIT 2"),
            {**params, "query_text": query_text},
        )
        .scalars()
        .all()
    )
    return rows[0] if len(rows) == 1 else None


@dataclass(frozen=True)
class StatusCount:
    status: str
    count: int


def count_documents_by_status(session: Session, scope: UserScope) -> list[StatusCount]:
    where_sql, params = _admin_hospital_scope_clause(scope)
    rows = session.execute(
        text(
            f"""
            SELECT status, count(*) AS count
            FROM documents
            WHERE {where_sql} AND is_active
            GROUP BY status
            ORDER BY count DESC, status ASC
            """
        ),
        params,
    ).mappings()
    return [StatusCount(**row) for row in rows]


def count_documents_total(session: Session, scope: UserScope) -> int:
    where_sql, params = _admin_hospital_scope_clause(scope)
    return session.execute(text(f"SELECT count(*) FROM documents WHERE {where_sql} AND is_active"), params).scalar_one()


@dataclass(frozen=True)
class AccessibleDocument:
    id: uuid.UUID
    title: str
    document_type: str


def count_documents_accessible_by_role(session: Session, scope: UserScope, role_name: str) -> int:
    where_sql, params = _admin_hospital_scope_clause(scope, column="doc.hospital_id")
    return session.execute(
        text(
            f"""
            SELECT count(*)
            FROM documents doc
            JOIN document_allowed_roles dar ON dar.document_id = doc.id
            JOIN roles r ON r.id = dar.role_id
            WHERE {where_sql} AND doc.is_active AND r.name = :role_name
            """
        ),
        {**params, "role_name": role_name},
    ).scalar_one()


def list_documents_accessible_by_role(
    session: Session, scope: UserScope, role_name: str, *, limit: int = ADMIN_LIST_LIMIT
) -> list[AccessibleDocument]:
    where_sql, params = _admin_hospital_scope_clause(scope, column="doc.hospital_id")
    rows = session.execute(
        text(
            f"""
            SELECT doc.id, doc.title, doc.document_type
            FROM documents doc
            JOIN document_allowed_roles dar ON dar.document_id = doc.id
            JOIN roles r ON r.id = dar.role_id
            WHERE {where_sql} AND doc.is_active AND r.name = :role_name
            ORDER BY doc.created_at DESC
            LIMIT :limit
            """
        ),
        {**params, "role_name": role_name, "limit": limit},
    ).mappings()
    return [AccessibleDocument(**row) for row in rows]
