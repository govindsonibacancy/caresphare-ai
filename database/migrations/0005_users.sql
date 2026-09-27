-- Application-level hospital user profile.
--
-- Identity/authentication itself belongs to Supabase Auth (Phase 3). This
-- table only stores the hospital-facing profile and links back to the
-- Supabase identity via auth_user_id. No password or credential material is
-- stored here or anywhere else in this database.

CREATE TABLE users (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Supabase Auth user id (auth.users.id). One application profile per
    -- Supabase identity.
    auth_user_id   UUID NOT NULL,
    hospital_id    UUID NOT NULL REFERENCES hospitals (id) ON DELETE RESTRICT,
    role_id        UUID NOT NULL REFERENCES roles (id) ON DELETE RESTRICT,
    -- Not every role is department-scoped (e.g. a hospital-wide admin), so
    -- this is optional.
    department_id  UUID,
    first_name     TEXT NOT NULL,
    last_name      TEXT NOT NULL,
    email          TEXT NOT NULL,
    phone          TEXT,
    is_active      BOOLEAN NOT NULL DEFAULT true,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT users_auth_user_id_key UNIQUE (auth_user_id),
    -- Referenced by composite foreign keys from doctors/staff so a doctor's
    -- or staff member's hospital always matches their user profile's hospital.
    CONSTRAINT users_id_hospital_key UNIQUE (id, hospital_id),
    -- A department, if set, must belong to the same hospital as the user.
    CONSTRAINT users_department_hospital_fkey
        FOREIGN KEY (department_id, hospital_id)
        REFERENCES departments (id, hospital_id)
        ON DELETE RESTRICT
);

-- Email is unique platform-wide (case-insensitive): a person authenticates
-- as a single Supabase identity regardless of which hospital they belong to.
CREATE UNIQUE INDEX users_email_lower_key ON users (lower(email));

CREATE INDEX idx_users_hospital_id ON users (hospital_id);
CREATE INDEX idx_users_role_id ON users (role_id);
CREATE INDEX idx_users_department_id ON users (department_id);

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
