-- Enables the PostgreSQL extensions CareSphere AI depends on.
-- Requires PostgreSQL with the pgvector extension available (e.g. the
-- pgvector/pgvector Docker image, or Supabase's Postgres which ships it).

CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "vector";    -- pgvector embedding column type
