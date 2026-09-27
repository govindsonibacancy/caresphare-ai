-- Phase 13: bounded multi-turn conversation support for POST
-- /api/rag/answer. See docs/CONVERSATIONAL_AUTH_ROUTING.md.
--
-- Deliberately minimal: two tables, no denormalized retrieval/source/
-- authorization state. `conversations` records ownership (who this
-- belongs to, and which hospital it was created under, for isolation);
-- `conversation_messages` records only the user/assistant message TEXT
-- needed for conversational continuity - never chunk content, structured
-- record fields, source registries, prompts, or SQL. Authorization is
-- never derived from this table - it is re-checked fresh on every turn
-- against current UserScope/database state (see
-- backend/app/llm/answer_service.py) - this schema exists purely to let a
-- follow-up question ("What department is that?") be understood, not to
-- grant access to anything.

CREATE TABLE conversations (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    hospital_id      UUID NOT NULL REFERENCES hospitals (id) ON DELETE RESTRICT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_activity_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_conversations_user_id ON conversations (user_id);
CREATE INDEX idx_conversations_hospital_id ON conversations (hospital_id);

CREATE TRIGGER trg_conversations_updated_at
    BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- One row per message, immutable once written (no updated_at/trigger -
-- there is no supported "edit a past message" operation). `seq` is a
-- global identity column (not a per-conversation counter) specifically so
-- ordering is deterministic under concurrent inserts without any
-- application-level locking - see docs/CONVERSATIONAL_AUTH_ROUTING.md,
-- "Concurrency".
CREATE TABLE conversation_messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    seq             BIGINT GENERATED ALWAYS AS IDENTITY,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT conversation_messages_role_check CHECK (role IN ('USER', 'ASSISTANT')),
    CONSTRAINT conversation_messages_content_not_blank_check CHECK (length(btrim(content)) > 0)
);

CREATE INDEX idx_conversation_messages_conversation_id_seq ON conversation_messages (conversation_id, seq);
