-- Doctor <-> patient care-team relationship.
--
-- This table is the source of truth Phase 4 authorization will use to decide
-- whether a doctor may access a given patient's records: a doctor does not
-- gain implicit access to every patient in the hospital just by working
-- there. Assignments are never deleted, only closed out via
-- unassigned_at/is_active, so history is preserved.

CREATE TABLE doctor_patient_assignments (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    doctor_id      UUID NOT NULL REFERENCES doctors (id) ON DELETE RESTRICT,
    patient_id     UUID NOT NULL REFERENCES patients (id) ON DELETE RESTRICT,
    assigned_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    unassigned_at  TIMESTAMPTZ,
    is_active      BOOLEAN NOT NULL DEFAULT true,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT doctor_patient_assignments_unassigned_after_assigned_check
        CHECK (unassigned_at IS NULL OR unassigned_at >= assigned_at)
);

-- Supports "does this doctor have an active assignment to this patient".
CREATE INDEX idx_dpa_doctor_id ON doctor_patient_assignments (doctor_id);
CREATE INDEX idx_dpa_patient_id ON doctor_patient_assignments (patient_id);
CREATE INDEX idx_dpa_is_active ON doctor_patient_assignments (is_active);
-- At most one *active* assignment per (doctor, patient) pair - re-assigning
-- the same doctor to the same patient after a prior assignment ended is
-- still allowed, it just creates a new row.
CREATE UNIQUE INDEX uq_dpa_active_doctor_patient
    ON doctor_patient_assignments (doctor_id, patient_id)
    WHERE is_active;

CREATE TRIGGER trg_dpa_updated_at
    BEFORE UPDATE ON doctor_patient_assignments
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
