-- RAG source-document metadata and its authorization scope. Ingestion,
-- embeddings, and retrieval are implemented in a later phase - this
-- migration only prepares the schema so permission-aware retrieval can be
-- built against it without a schema change.
--
-- Authorization scope is deliberately split across normalized tables rather
-- than array columns: `allowed_roles` -> document_allowed_roles,
-- `authorized_doctor_ids` -> document_authorized_doctors, and
-- `authorized_nurse_ids` -> document_authorized_staff (nurses are `staff`
-- rows, same as in the rest of this schema - there is no separate "nurses"
-- table). Normalized join tables give referential integrity (an authorized
-- doctor id is guaranteed to be a real doctor) and are indexable, which a
-- plain array column is not without a GIN index and extra query complexity.
--
-- The backend must still enforce these rules before retrieval; nothing here
-- makes retrieval self-authorizing.

CREATE TABLE documents (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_id    UUID NOT NULL REFERENCES hospitals (id) ON DELETE RESTRICT,
    department_id  UUID,
    title          TEXT NOT NULL,
    filename       TEXT NOT NULL,
    document_type  TEXT NOT NULL,
    description    TEXT,
    -- Coarse-grained default visibility. `document_allowed_roles` /
    -- `document_authorized_doctors` / `document_authorized_staff` narrow it
    -- further; PATIENT_SPECIFIC documents also carry a patient_id.
    sensitivity    TEXT NOT NULL,
    patient_id     UUID,
    uploaded_by    UUID NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    status         TEXT NOT NULL DEFAULT 'PENDING',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT documents_document_type_check CHECK (
        document_type IN (
            'HOSPITAL_POLICY', 'CLINICAL_GUIDELINE', 'NURSING_PROCEDURE',
            'MEDICATION_GUIDELINE', 'EMERGENCY_PROCEDURE', 'PATIENT_EDUCATION',
            'HR_POLICY', 'SOP', 'GENERAL_INFORMATION'
        )
    ),
    CONSTRAINT documents_sensitivity_check CHECK (
        sensitivity IN ('PUBLIC', 'INTERNAL', 'CLINICAL', 'CONFIDENTIAL', 'PATIENT_SPECIFIC')
    ),
    CONSTRAINT documents_status_check CHECK (
        status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED')
    ),
    -- A PATIENT_SPECIFIC document must name the patient it belongs to; no
    -- other sensitivity level should carry a patient_id.
    CONSTRAINT documents_patient_specific_check CHECK (
        (sensitivity = 'PATIENT_SPECIFIC' AND patient_id IS NOT NULL)
        OR (sensitivity <> 'PATIENT_SPECIFIC' AND patient_id IS NULL)
    ),
    CONSTRAINT documents_id_hospital_key UNIQUE (id, hospital_id),
    CONSTRAINT documents_department_hospital_fkey
        FOREIGN KEY (department_id, hospital_id) REFERENCES departments (id, hospital_id)
        ON DELETE RESTRICT,
    CONSTRAINT documents_patient_hospital_fkey
        FOREIGN KEY (patient_id, hospital_id) REFERENCES patients (id, hospital_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_documents_hospital_id ON documents (hospital_id);
CREATE INDEX idx_documents_department_id ON documents (department_id);
CREATE INDEX idx_documents_status ON documents (status);

CREATE TRIGGER trg_documents_updated_at
    BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Roles allowed to see this document by default (e.g. a HOSPITAL_POLICY
-- document allowed for STAFF and HOSPITAL_ADMIN).
CREATE TABLE document_allowed_roles (
    document_id  UUID NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    role_id      UUID NOT NULL REFERENCES roles (id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, role_id)
);

CREATE INDEX idx_document_allowed_roles_role_id ON document_allowed_roles (role_id);

-- Specific doctors authorized beyond default role-based access (typically
-- used for PATIENT_SPECIFIC documents shared with the assigned doctor).
CREATE TABLE document_authorized_doctors (
    document_id  UUID NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    doctor_id    UUID NOT NULL REFERENCES doctors (id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, doctor_id)
);

CREATE INDEX idx_document_authorized_doctors_doctor_id ON document_authorized_doctors (doctor_id);

-- Specific staff (typically nurses, identified by staff.designation)
-- authorized beyond default role-based access.
CREATE TABLE document_authorized_staff (
    document_id  UUID NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    staff_id     UUID NOT NULL REFERENCES staff (id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, staff_id)
);

CREATE INDEX idx_document_authorized_staff_staff_id ON document_authorized_staff (staff_id);
