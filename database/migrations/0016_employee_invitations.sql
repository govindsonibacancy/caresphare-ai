-- Employee onboarding: a HOSPITAL_ADMIN (or SUPER_ADMIN) creates an
-- invitation naming a role/department; the invitee accepts it to become a
-- provisioned application user. Employees are never publicly
-- self-registerable - see docs/EMPLOYEE_INVITATIONS.md.
--
-- The raw invitation token is never stored - only its hash
-- (invitation_token_hash). The raw token exists only in the URL emailed to
-- the invitee; see backend/app/auth/invitation_tokens.py.

CREATE TABLE employee_invitations (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invitation_token_hash  TEXT NOT NULL,
    email                  TEXT NOT NULL,
    first_name             TEXT NOT NULL,
    last_name              TEXT NOT NULL,
    hospital_id            UUID NOT NULL REFERENCES hospitals (id) ON DELETE RESTRICT,
    department_id          UUID,
    role_id                UUID NOT NULL REFERENCES roles (id) ON DELETE RESTRICT,
    invited_by_user_id     UUID NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    -- Set only once status = ACCEPTED - the application user that resulted
    -- from this invitation. RESTRICT (not SET NULL): the
    -- accepted-consistency check below requires an ACCEPTED row to always
    -- have an accepted_user_id, so nulling it out on user deletion would
    -- leave the row violating its own constraint.
    accepted_user_id       UUID REFERENCES users (id) ON DELETE RESTRICT,
    status                 TEXT NOT NULL DEFAULT 'PENDING',
    expires_at             TIMESTAMPTZ NOT NULL,
    accepted_at            TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT employee_invitations_token_hash_key UNIQUE (invitation_token_hash),
    CONSTRAINT employee_invitations_status_check CHECK (
        status IN ('PENDING', 'ACCEPTED', 'EXPIRED', 'REVOKED')
    ),
    CONSTRAINT employee_invitations_accepted_consistency_check CHECK (
        (status = 'ACCEPTED' AND accepted_at IS NOT NULL AND accepted_user_id IS NOT NULL)
        OR (status <> 'ACCEPTED' AND accepted_at IS NULL AND accepted_user_id IS NULL)
    ),
    -- A department, if given, must belong to the same hospital as the
    -- invitation itself - the same composite-FK pattern Phase 2 uses
    -- everywhere else for hospital isolation.
    CONSTRAINT employee_invitations_department_hospital_fkey
        FOREIGN KEY (department_id, hospital_id)
        REFERENCES departments (id, hospital_id)
        ON DELETE RESTRICT
);

-- Policy: at most one PENDING invitation per (hospital, email) - a second
-- create attempt for the same not-yet-accepted invitee is rejected, not
-- silently duplicated. This is also what makes invitation creation safe
-- under concurrent requests, since it's enforced by the index itself, not
-- just an application-level check-then-insert.
CREATE UNIQUE INDEX employee_invitations_pending_hospital_email_key
    ON employee_invitations (hospital_id, lower(email))
    WHERE status = 'PENDING';

CREATE INDEX idx_employee_invitations_hospital_id ON employee_invitations (hospital_id);
CREATE INDEX idx_employee_invitations_status ON employee_invitations (status);
CREATE INDEX idx_employee_invitations_expires_at ON employee_invitations (expires_at);
CREATE INDEX idx_employee_invitations_email ON employee_invitations (lower(email));

CREATE TRIGGER trg_employee_invitations_updated_at
    BEFORE UPDATE ON employee_invitations
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
