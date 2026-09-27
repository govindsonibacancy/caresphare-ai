-- Phase 17: conversation history & persistent chat UX.
--
-- Two small, additive columns - no new table, no data reset, no change to
-- any existing row's meaning:
--
-- conversations.title: a short, deterministic label derived from the
-- conversation's first user message (see
-- app/services/conversation_service.py) - never LLM-generated, never
-- containing anything beyond what the user themselves already typed.
-- NULL until the first turn completes (existing conversations, and a
-- brand-new one before its first message, simply have no title yet).
--
-- conversation_messages.sources: the same `SourceReference` list already
-- returned to the client in the API response for that turn (Phase 12),
-- persisted so a historical assistant message can render its citations
-- identically to a freshly-generated one - see docs/FRONTEND_AI_CHAT.md,
-- "Sources and citations". Never raw chunk/document content (SourceReference
-- never carries any); defaults to an empty array so every pre-Phase-17 row
-- (and every USER-role row, which never has sources) reads back cleanly.

ALTER TABLE conversations ADD COLUMN title TEXT;

ALTER TABLE conversation_messages ADD COLUMN sources JSONB NOT NULL DEFAULT '[]'::jsonb;

-- Supports "list this user's conversations, most recently active first" -
-- the exact query the new GET /api/conversations endpoint runs.
CREATE INDEX idx_conversations_user_id_last_activity_at ON conversations (user_id, last_activity_at DESC);
