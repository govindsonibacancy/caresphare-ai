-- Phase 7 (RAG ingestion) needs a handful of fields Phase 2's `documents`
-- table didn't yet have: the concrete file fingerprint/size/type the
-- ingestion pipeline produces, a safe place for a processing failure
-- message, and a way to retire a document without losing its history.
--
-- No new tables: `documents`/`document_chunks`/`document_allowed_roles`/
-- `document_authorized_doctors`/`document_authorized_staff` (0012, 0013)
-- and their status vocabulary (PENDING/PROCESSING/COMPLETED/FAILED) are
-- reused exactly as they are - see docs/RAG_INGESTION.md.

ALTER TABLE documents
    ADD COLUMN mime_type        TEXT,
    ADD COLUMN file_size        BIGINT,
    ADD COLUMN content_hash     TEXT,
    ADD COLUMN processing_error TEXT,
    ADD COLUMN is_active        BOOLEAN NOT NULL DEFAULT true;

-- mime_type/file_size/content_hash are set once, at upload time, from the
-- actual uploaded bytes - not nullable in spirit, but added as nullable
-- ALTER COLUMNs (no existing rows to backfill; Phase 2/6 never inserted any
-- documents) and left non-NOT-NULL only because Postgres would otherwise
-- require a DEFAULT for a column added to a table that could have rows.
-- The application always supplies all three on insert.

-- At most one *active* document per (hospital, content_hash) - re-uploading
-- byte-identical content is rejected rather than silently duplicated; see
-- docs/RAG_INGESTION.md, "Idempotency". Mirrors the partial-unique-index
-- pattern already used for employee_invitations (0016).
CREATE UNIQUE INDEX documents_hospital_content_hash_active_key
    ON documents (hospital_id, content_hash)
    WHERE is_active AND content_hash IS NOT NULL;

CREATE INDEX idx_documents_is_active ON documents (is_active);
