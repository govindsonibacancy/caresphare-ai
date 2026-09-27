import datetime
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class DepartmentSummary:
    id: uuid.UUID
    name: str
    code: str


def list_departments(db: Session, hospital_id: uuid.UUID) -> list[DepartmentSummary]:
    rows = db.execute(
        text(
            "SELECT id, name, code FROM departments WHERE hospital_id = :hospital_id AND is_active ORDER BY name"
        ),
        {"hospital_id": hospital_id},
    ).mappings().all()
    return [DepartmentSummary(**row) for row in rows]

_SELECT_INVITATION = """
    SELECT ei.id, ei.email, ei.first_name, ei.last_name,
           ei.hospital_id, ei.department_id, d.name AS department_name,
           ei.role_id, r.name AS role, ei.invited_by_user_id,
           ei.accepted_user_id, ei.status, ei.expires_at, ei.accepted_at,
           ei.created_at
    FROM employee_invitations ei
    JOIN roles r ON r.id = ei.role_id
    LEFT JOIN departments d ON d.id = ei.department_id
    WHERE {where}
"""


@dataclass(frozen=True)
class InvitationRecord:
    id: uuid.UUID
    email: str
    first_name: str
    last_name: str
    hospital_id: uuid.UUID
    department_id: uuid.UUID | None
    department_name: str | None
    role_id: uuid.UUID
    role: str
    invited_by_user_id: uuid.UUID
    accepted_user_id: uuid.UUID | None
    status: str
    expires_at: datetime.datetime
    accepted_at: datetime.datetime | None
    created_at: datetime.datetime


def get_role_id_by_name(db: Session, role_name: str) -> uuid.UUID | None:
    return db.execute(text("SELECT id FROM roles WHERE name = :name"), {"name": role_name}).scalar_one_or_none()


def get_hospital_name(db: Session, hospital_id: uuid.UUID) -> str:
    return db.execute(text("SELECT name FROM hospitals WHERE id = :id"), {"id": hospital_id}).scalar_one()


def department_belongs_to_hospital(db: Session, department_id: uuid.UUID, hospital_id: uuid.UUID) -> bool:
    return (
        db.execute(
            text("SELECT 1 FROM departments WHERE id = :department_id AND hospital_id = :hospital_id"),
            {"department_id": department_id, "hospital_id": hospital_id},
        ).first()
        is not None
    )


def expire_stale_pending(db: Session) -> None:
    """Lazily flips any PENDING invitation past its expiry to EXPIRED. Run
    opportunistically before reads (list/get-by-token) rather than via a
    background job - see docs/EMPLOYEE_INVITATIONS.md, "Invitation
    expiration". Idempotent and cheap (indexed on status, expires_at).
    """
    db.execute(
        text(
            "UPDATE employee_invitations SET status = 'EXPIRED' "
            "WHERE status = 'PENDING' AND expires_at < now()"
        )
    )


def create_invitation(
    db: Session,
    *,
    token_hash: str,
    email: str,
    first_name: str,
    last_name: str,
    hospital_id: uuid.UUID,
    department_id: uuid.UUID | None,
    role_id: uuid.UUID,
    invited_by_user_id: uuid.UUID,
    expires_at: datetime.datetime,
) -> InvitationRecord:
    invitation_id = db.execute(
        text(
            """
            INSERT INTO employee_invitations (
                invitation_token_hash, email, first_name, last_name,
                hospital_id, department_id, role_id, invited_by_user_id, expires_at
            )
            VALUES (
                :token_hash, :email, :first_name, :last_name,
                :hospital_id, :department_id, :role_id, :invited_by_user_id, :expires_at
            )
            RETURNING id
            """
        ),
        {
            "token_hash": token_hash,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "hospital_id": hospital_id,
            "department_id": department_id,
            "role_id": role_id,
            "invited_by_user_id": invited_by_user_id,
            "expires_at": expires_at,
        },
    ).scalar_one()
    record = get_invitation_by_id(db, invitation_id)
    assert record is not None
    return record


def get_invitation_by_id(db: Session, invitation_id: uuid.UUID) -> InvitationRecord | None:
    row = db.execute(text(_SELECT_INVITATION.format(where="ei.id = :id")), {"id": invitation_id}).mappings().first()
    return InvitationRecord(**row) if row else None


def get_invitation_by_token_hash(db: Session, token_hash: str) -> InvitationRecord | None:
    row = (
        db.execute(text(_SELECT_INVITATION.format(where="ei.invitation_token_hash = :h")), {"h": token_hash})
        .mappings()
        .first()
    )
    return InvitationRecord(**row) if row else None


def get_invitation_by_token_hash_for_update(db: Session, token_hash: str) -> InvitationRecord | None:
    """Locks the row (SELECT ... FOR UPDATE) for the duration of the calling
    transaction, so two concurrent accept attempts for the same invitation
    serialize: the second blocks until the first commits, then observes the
    now-non-PENDING status and cleanly fails - see
    app/services/invitation_service.accept_invitation and
    backend/tests/test_invitation_acceptance.py's concurrency test.
    """
    row = (
        db.execute(
            text(_SELECT_INVITATION.format(where="ei.invitation_token_hash = :h") + " FOR UPDATE OF ei"),
            {"h": token_hash},
        )
        .mappings()
        .first()
    )
    return InvitationRecord(**row) if row else None


def list_invitations(
    db: Session,
    *,
    hospital_id: uuid.UUID,
    status: str | None = None,
    role: str | None = None,
    department_id: uuid.UUID | None = None,
    email: str | None = None,
) -> list[InvitationRecord]:
    clauses = ["ei.hospital_id = :hospital_id"]
    params: dict[str, object] = {"hospital_id": hospital_id}
    if status is not None:
        clauses.append("ei.status = :status")
        params["status"] = status
    if role is not None:
        clauses.append("r.name = :role")
        params["role"] = role
    if department_id is not None:
        clauses.append("ei.department_id = :department_id")
        params["department_id"] = department_id
    if email is not None:
        clauses.append("lower(ei.email) = lower(:email)")
        params["email"] = email

    sql = _SELECT_INVITATION.format(where=" AND ".join(clauses)) + " ORDER BY ei.created_at DESC"
    rows = db.execute(text(sql), params).mappings().all()
    return [InvitationRecord(**row) for row in rows]


def mark_accepted(db: Session, invitation_id: uuid.UUID, accepted_user_id: uuid.UUID) -> None:
    db.execute(
        text(
            """
            UPDATE employee_invitations
            SET status = 'ACCEPTED', accepted_at = now(), accepted_user_id = :accepted_user_id
            WHERE id = :id
            """
        ),
        {"id": invitation_id, "accepted_user_id": accepted_user_id},
    )


def mark_revoked(db: Session, invitation_id: uuid.UUID) -> None:
    db.execute(
        text("UPDATE employee_invitations SET status = 'REVOKED' WHERE id = :id"),
        {"id": invitation_id},
    )


def mark_expired(db: Session, invitation_id: uuid.UUID) -> None:
    db.execute(
        text("UPDATE employee_invitations SET status = 'EXPIRED' WHERE id = :id"),
        {"id": invitation_id},
    )
