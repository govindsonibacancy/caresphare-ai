# Development setup

## Prerequisites

- Node.js 20+ and npm
- Python 3.11+
- PostgreSQL 17 + pgvector (`brew install postgresql@17 pgvector`), Docker,
  or an existing Postgres with the `vector` extension - see
  [database/README.md](../database/README.md)
- A [Supabase](https://supabase.com) project (free tier is fine), for
  authentication - see "Authentication" below. The app still builds/runs
  without one, but sign-up/sign-in calls will fail.
- [Ollama](https://ollama.com) installed locally and running, with
  `ollama pull all-minilm` (384-dimension embeddings - required for both
  RAG document upload and RAG search to succeed; see
  [docs/RAG_INGESTION.md](RAG_INGESTION.md) and
  [docs/RAG_RETRIEVAL.md](RAG_RETRIEVAL.md)) and `ollama pull llama3.2:3b`
  (the default LLM answer-generation model - required for
  `POST /api/rag/answer` to succeed; see
  [docs/LLM_GENERATION.md](LLM_GENERATION.md)). The automated test suite
  does not require the LLM model to be pulled (Ollama's chat endpoint is
  mocked in tests), but does require `all-minilm` for the RAG tests that
  upload real documents.

## Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env

uvicorn app.main:app --reload
```

- API: http://localhost:8000
- Interactive docs: http://localhost:8000/docs
- Health check: `GET /health`

Run tests:

```bash
cd backend
source .venv/bin/activate
pytest
```

## Frontend

```bash
cd frontend
npm install
cp .env.example .env

npm run dev
```

- App: http://localhost:5173
- The app shell shows a live "backend: online/offline" indicator driven by
  `GET /health` on the backend.

Build and lint:

```bash
cd frontend
npm run build
npm run lint
```

## Database

See [database/README.md](../database/README.md) for local Postgres +
pgvector setup and the migration layout.

## Authentication

Both apps need the same Supabase project's credentials:

- Backend `.env`: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (Settings →
  API in the Supabase dashboard; `SUPABASE_JWT_AUDIENCE` defaults to
  `authenticated` and rarely needs changing).
- Frontend `.env`: `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` (same
  project, public anon key only - never put the service-role key here).

Seeded demo users (`database/seeds/0001_demo_data.sql`) are **not** real
Supabase accounts - their `auth_user_id` is a placeholder. See
[database/README.md](../database/README.md#synthetic-data-policy) for how to
attach a real Supabase identity to one for local testing, or just register a
new patient via `/register`.

## Project layout

```
frontend/   React + TypeScript + Vite
backend/    FastAPI + Pydantic
database/   SQL migrations, seed data (synthetic only)
docs/       Architecture and development docs
```

See [docs/ARCHITECTURE.md](./ARCHITECTURE.md) for how the pieces fit
together and the authorization-before-retrieval design.
