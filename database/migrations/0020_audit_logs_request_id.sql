-- Phase 15: a top-level, indexed request_id column for audit_logs, so
-- "find every event for request X" (docs/AUDIT_AND_OBSERVABILITY.md,
-- "Audit queryability") is an indexed lookup, not a JSONB scan. Nullable
-- and deliberately not a foreign key - a request_id is a correlation
-- value generated per HTTP request, not a reference to another table.
-- Existing rows (written before this phase) simply have NULL here, which
-- is correct: they were never part of a traced request.

ALTER TABLE audit_logs ADD COLUMN request_id UUID;

CREATE INDEX idx_audit_logs_request_id ON audit_logs (request_id);
