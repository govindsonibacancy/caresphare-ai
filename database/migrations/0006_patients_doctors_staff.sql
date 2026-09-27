-- Clinical-role tables. `users` holds the login-capable profile shared by
-- every role; these tables hold the role-specific data and link back to
-- `users` (except patients, who may not have a portal login at all).

CREATE TABLE patients (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Nullable: a patient record can exist before the patient ever has a
    -- Supabase-backed portal login (e.g. registered at the front desk).
    user_id                 UUID REFERENCES users (id) ON DELETE SET NULL,
    hospital_id             UUID NOT NULL REFERENCES hospitals (id) ON DELETE RESTRICT,
    patient_number          TEXT NOT NULL,
    date_of_birth           DATE,
    gender                  TEXT,
    blood_group             TEXT,
    phone                   TEXT,
    address                 TEXT,
    emergency_contact_name  TEXT,
    emergency_contact_phone TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Patient numbers are issued per-hospital, so uniqueness is scoped to
    -- the hospital rather than global.
    CONSTRAINT patients_hospital_patient_number_key UNIQUE (hospital_id, patient_number),
    -- Referenced by composite foreign keys from appointments/medical_records/
    -- lab_reports/prescriptions/documents to keep those rows in the same
    -- hospital as the patient they reference.
    CONSTRAINT patients_id_hospital_key UNIQUE (id, hospital_id)
);

CREATE INDEX idx_patients_hospital_id ON patients (hospital_id);
CREATE INDEX idx_patients_patient_number ON patients (patient_number);

CREATE TRIGGER trg_patients_updated_at
    BEFORE UPDATE ON patients
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE doctors (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    hospital_id      UUID NOT NULL REFERENCES hospitals (id) ON DELETE RESTRICT,
    department_id    UUID,
    employee_number  TEXT NOT NULL,
    specialization   TEXT,
    license_number   TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT doctors_user_id_key UNIQUE (user_id),
    CONSTRAINT doctors_hospital_employee_number_key UNIQUE (hospital_id, employee_number),
    CONSTRAINT doctors_id_hospital_key UNIQUE (id, hospital_id),
    CONSTRAINT doctors_department_hospital_fkey
        FOREIGN KEY (department_id, hospital_id)
        REFERENCES departments (id, hospital_id)
        ON DELETE RESTRICT,
    -- A doctor's user profile must belong to the same hospital as the
    -- doctor record itself.
    CONSTRAINT doctors_user_hospital_fkey
        FOREIGN KEY (user_id, hospital_id)
        REFERENCES users (id, hospital_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_doctors_hospital_id ON doctors (hospital_id);
CREATE INDEX idx_doctors_department_id ON doctors (department_id);

CREATE TRIGGER trg_doctors_updated_at
    BEFORE UPDATE ON doctors
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Nurses, receptionists, and other non-clinician hospital staff. A single
-- table (distinguished by the linked user's role and this row's
-- `designation`) rather than one table per job title, since they share the
-- same shape and none of them need doctor-specific fields.
CREATE TABLE staff (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    hospital_id      UUID NOT NULL REFERENCES hospitals (id) ON DELETE RESTRICT,
    department_id    UUID,
    employee_number  TEXT NOT NULL,
    designation      TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT staff_user_id_key UNIQUE (user_id),
    CONSTRAINT staff_hospital_employee_number_key UNIQUE (hospital_id, employee_number),
    CONSTRAINT staff_id_hospital_key UNIQUE (id, hospital_id),
    CONSTRAINT staff_department_hospital_fkey
        FOREIGN KEY (department_id, hospital_id)
        REFERENCES departments (id, hospital_id)
        ON DELETE RESTRICT,
    CONSTRAINT staff_user_hospital_fkey
        FOREIGN KEY (user_id, hospital_id)
        REFERENCES users (id, hospital_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_staff_hospital_id ON staff (hospital_id);
CREATE INDEX idx_staff_department_id ON staff (department_id);

CREATE TRIGGER trg_staff_updated_at
    BEFORE UPDATE ON staff
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
