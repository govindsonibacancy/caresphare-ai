-- Data model only - no clinical decision-making happens here or anywhere
-- else in this codebase.

CREATE TABLE medical_records (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_id     UUID NOT NULL REFERENCES hospitals (id) ON DELETE RESTRICT,
    patient_id      UUID NOT NULL,
    doctor_id       UUID NOT NULL,
    record_type     TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    clinical_notes  TEXT,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT medical_records_record_type_check CHECK (
        record_type IN ('CONSULTATION', 'DIAGNOSIS', 'FOLLOW_UP', 'DISCHARGE', 'CLINICAL_NOTE')
    ),
    CONSTRAINT medical_records_patient_hospital_fkey
        FOREIGN KEY (patient_id, hospital_id) REFERENCES patients (id, hospital_id)
        ON DELETE RESTRICT,
    CONSTRAINT medical_records_doctor_hospital_fkey
        FOREIGN KEY (doctor_id, hospital_id) REFERENCES doctors (id, hospital_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_medical_records_hospital_id ON medical_records (hospital_id);
CREATE INDEX idx_medical_records_patient_id ON medical_records (patient_id);
CREATE INDEX idx_medical_records_doctor_id ON medical_records (doctor_id);
CREATE INDEX idx_medical_records_recorded_at ON medical_records (recorded_at);

CREATE TRIGGER trg_medical_records_updated_at
    BEFORE UPDATE ON medical_records
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
