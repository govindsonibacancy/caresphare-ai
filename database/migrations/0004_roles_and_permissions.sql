-- RBAC data model: roles, granular permissions, and the many-to-many between
-- them. No authorization logic runs against this data yet (that's Phase 4) -
-- this migration only makes the data available for that later phase.

CREATE TABLE roles (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Stable, application-facing identifier (matches the Role enum used by
    -- the backend and frontend). Fixed to the seven known roles so a typo
    -- can't silently create an eighth role.
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT roles_name_key UNIQUE (name),
    CONSTRAINT roles_name_check CHECK (
        name IN (
            'PATIENT', 'DOCTOR', 'NURSE', 'RECEPTIONIST', 'STAFF',
            'HOSPITAL_ADMIN', 'SUPER_ADMIN'
        )
    )
);

CREATE TRIGGER trg_roles_updated_at
    BEFORE UPDATE ON roles
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE permissions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code        TEXT NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT permissions_code_key UNIQUE (code)
);

CREATE TRIGGER trg_permissions_updated_at
    BEFORE UPDATE ON permissions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE role_permissions (
    role_id       UUID NOT NULL REFERENCES roles (id) ON DELETE CASCADE,
    permission_id UUID NOT NULL REFERENCES permissions (id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (role_id, permission_id)
);
-- role_permissions rows have no independent meaning without their role or
-- permission, so cascading here (unlike clinical/audit tables) does not risk
-- losing historical records - it only removes an assignment that is itself
-- being removed.

CREATE INDEX idx_role_permissions_permission_id ON role_permissions (permission_id);
