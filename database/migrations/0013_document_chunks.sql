-- Chunked, embeddable content for each document. Embedding generation and
-- vector search are implemented in a later phase - this migration only
-- prepares the column.
--
-- Vector dimension: 384, matching sentence-transformers/all-MiniLM-L6-v2,
-- the default EMBEDDING_MODEL in backend/app/core/config.py. pgvector fixes
-- the dimension per column, so switching embedding models later requires a
-- migration that adds a new column (or table) at the new dimension and
-- re-embeds existing chunks - it cannot be changed in place.

CREATE TABLE document_chunks (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id  UUID NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    chunk_index  INT NOT NULL,
    content      TEXT NOT NULL,
    page_number  INT,
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding    VECTOR(384),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT document_chunks_document_chunk_index_key UNIQUE (document_id, chunk_index)
);

CREATE INDEX idx_document_chunks_document_id ON document_chunks (document_id);

-- No ANN index (ivfflat/hnsw) on `embedding` yet: this phase does not
-- populate the column, and both index types are tuned against a populated
-- table. The RAG ingestion phase should add one once there is real data,
-- e.g.:
--   CREATE INDEX ON document_chunks USING hnsw (embedding vector_cosine_ops);

CREATE TRIGGER trg_document_chunks_updated_at
    BEFORE UPDATE ON document_chunks
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
