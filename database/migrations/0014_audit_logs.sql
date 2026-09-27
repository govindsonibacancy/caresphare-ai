-- Audit trail for data access and administrative actions. Logs reference
-- resources by id rather than copying their content, and never contain
-- credentials or full record bodies - see database/README.md for the policy.

CREATE TABLE audit_logs (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Nullable and ON DELETE SET NULL (unlike every other user reference in
    -- this schema, which is RESTRICT): an audit row must never become
    -- undeletable-by-proxy just because it references a user, and it must
    -- survive even if the actor reference is later cleared.
    actor_user_id  UUID REFERENCES users (id) ON DELETE SET NULL,
    action         TEXT NOT NULL,
    -- resource_type/resource_id are a polymorphic reference (a patient, a
    -- document, an appointment, ...). Deliberately not a foreign key: the
    -- target table varies per action, and integrity there is secondary to
    -- audit rows being immutable and always retained.
    resource_type  TEXT,
    resource_id    UUID,
    hospital_id    UUID REFERENCES hospitals (id) ON DELETE SET NULL,
    metadata       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_logs_actor_user_id ON audit_logs (actor_user_id);
CREATE INDEX idx_audit_logs_hospital_id ON audit_logs (hospital_id);
CREATE INDEX idx_audit_logs_created_at ON audit_logs (created_at);
