-- Data model only - no prescription generation or medical recommendation
-- logic lives here or anywhere else in this codebase.

CREATE TABLE prescriptions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_id       UUID NOT NULL REFERENCES hospitals (id) ON DELETE RESTRICT,
    patient_id        UUID NOT NULL,
    doctor_id         UUID NOT NULL,
    medication_name   TEXT NOT NULL,
    dosage            TEXT,
    frequency         TEXT,
    route             TEXT,
    duration          TEXT,
    instructions      TEXT,
    prescribed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT prescriptions_patient_hospital_fkey
        FOREIGN KEY (patient_id, hospital_id) REFERENCES patients (id, hospital_id)
        ON DELETE RESTRICT,
    CONSTRAINT prescriptions_doctor_hospital_fkey
        FOREIGN KEY (doctor_id, hospital_id) REFERENCES doctors (id, hospital_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_prescriptions_hospital_id ON prescriptions (hospital_id);
CREATE INDEX idx_prescriptions_patient_id ON prescriptions (patient_id);

CREATE TRIGGER trg_prescriptions_updated_at
    BEFORE UPDATE ON prescriptions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
