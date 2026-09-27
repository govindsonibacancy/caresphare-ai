CREATE TABLE lab_reports (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_id           UUID NOT NULL REFERENCES hospitals (id) ON DELETE RESTRICT,
    patient_id            UUID NOT NULL,
    ordered_by_doctor_id  UUID NOT NULL,
    test_name             TEXT NOT NULL,
    test_code             TEXT,
    result                TEXT,
    unit                  TEXT,
    reference_range       TEXT,
    status                TEXT NOT NULL DEFAULT 'ORDERED',
    reported_at           TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT lab_reports_status_check CHECK (
        status IN ('ORDERED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')
    ),
    CONSTRAINT lab_reports_patient_hospital_fkey
        FOREIGN KEY (patient_id, hospital_id) REFERENCES patients (id, hospital_id)
        ON DELETE RESTRICT,
    CONSTRAINT lab_reports_doctor_hospital_fkey
        FOREIGN KEY (ordered_by_doctor_id, hospital_id) REFERENCES doctors (id, hospital_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_lab_reports_hospital_id ON lab_reports (hospital_id);
CREATE INDEX idx_lab_reports_patient_id ON lab_reports (patient_id);

CREATE TRIGGER trg_lab_reports_updated_at
    BEFORE UPDATE ON lab_reports
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
