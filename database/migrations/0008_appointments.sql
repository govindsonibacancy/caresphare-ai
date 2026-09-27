CREATE TABLE appointments (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_id        UUID NOT NULL REFERENCES hospitals (id) ON DELETE RESTRICT,
    patient_id         UUID NOT NULL,
    doctor_id          UUID NOT NULL,
    department_id      UUID,
    appointment_date   DATE NOT NULL,
    appointment_time   TIME NOT NULL,
    -- Text + CHECK rather than a native enum: easy to extend in a later
    -- migration (ALTER TABLE ... DROP/ADD CONSTRAINT) without the multi-step
    -- process Postgres enum types require.
    status             TEXT NOT NULL DEFAULT 'SCHEDULED',
    reason             TEXT,
    notes              TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT appointments_status_check CHECK (
        status IN ('SCHEDULED', 'CONFIRMED', 'COMPLETED', 'CANCELLED', 'NO_SHOW')
    ),
    -- Keep the patient, doctor, and department in the same hospital as the
    -- appointment itself.
    CONSTRAINT appointments_patient_hospital_fkey
        FOREIGN KEY (patient_id, hospital_id) REFERENCES patients (id, hospital_id)
        ON DELETE RESTRICT,
    CONSTRAINT appointments_doctor_hospital_fkey
        FOREIGN KEY (doctor_id, hospital_id) REFERENCES doctors (id, hospital_id)
        ON DELETE RESTRICT,
    CONSTRAINT appointments_department_hospital_fkey
        FOREIGN KEY (department_id, hospital_id) REFERENCES departments (id, hospital_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_appointments_hospital_id ON appointments (hospital_id);
CREATE INDEX idx_appointments_patient_id ON appointments (patient_id);
CREATE INDEX idx_appointments_doctor_id ON appointments (doctor_id);
CREATE INDEX idx_appointments_appointment_date ON appointments (appointment_date);

CREATE TRIGGER trg_appointments_updated_at
    BEFORE UPDATE ON appointments
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
