-- Hospital organization: hospitals and their departments.
-- Every hospital-scoped table elsewhere references hospitals.id (and, where a
-- department applies, departments.id) to keep hospital data isolated.

CREATE TABLE hospitals (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    code        TEXT NOT NULL,
    address     TEXT,
    city        TEXT,
    state       TEXT,
    country     TEXT,
    phone       TEXT,
    email       TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT hospitals_code_key UNIQUE (code)
);

CREATE TRIGGER trg_hospitals_updated_at
    BEFORE UPDATE ON hospitals
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE departments (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_id  UUID NOT NULL REFERENCES hospitals (id) ON DELETE RESTRICT,
    name         TEXT NOT NULL,
    code         TEXT NOT NULL,
    description  TEXT,
    is_active    BOOLEAN NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- One department code per hospital (not globally unique - two hospitals
    -- can both have a "CARD" department).
    CONSTRAINT departments_hospital_code_key UNIQUE (hospital_id, code),
    -- Referenced by composite foreign keys from users/doctors/staff/etc. so
    -- that a row's department is verified to belong to the same hospital.
    CONSTRAINT departments_id_hospital_key UNIQUE (id, hospital_id)
);

CREATE INDEX idx_departments_hospital_id ON departments (hospital_id);

CREATE TRIGGER trg_departments_updated_at
    BEFORE UPDATE ON departments
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
