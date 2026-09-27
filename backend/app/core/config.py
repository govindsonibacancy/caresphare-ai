from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration. Values come from .env / the process environment."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "CareSphere AI"
    environment: str = "development"

    # PostgreSQL (+ pgvector). Uses the psycopg (v3) driver.
    database_url: str = "postgresql+psycopg://caresphere:caresphere@localhost:5432/caresphere"

    # Supabase Auth. The backend verifies access tokens against the
    # project's JWKS (signature + issuer + audience + expiration) rather
    # than trusting a decoded payload - see app/auth/jwt_verifier.py.
    supabase_url: str = ""
    supabase_jwt_issuer: str = ""  # defaults to f"{supabase_url}/auth/v1" if empty
    supabase_jwt_audience: str = "authenticated"
    # Backend-only. Never expose via VITE_* or any frontend code. Used only
    # to verify a just-created Supabase identity during patient registration
    # and employee-invitation acceptance (app/auth/supabase_admin.py) - the
    # key itself is never returned to a client.
    supabase_service_role_key: str = ""

    # Hospital assigned to self-registered patients. See database/README.md
    # ("Patient registration hospital assignment").
    default_patient_hospital_code: str = "CGH"

    # Employee invitations (Phase 5). See docs/EMPLOYEE_INVITATIONS.md.
    employee_invitation_expiry_days: int = 7
    # Used to build the invitation URL emailed to the invitee
    # (<frontend_base_url>/accept-invitation?token=...).
    frontend_base_url: str = "http://localhost:5173"

    # Ollama
    ollama_base_url: str = "http://localhost:11434"

    # LLM answer generation (Phase 10). See docs/LLM_GENERATION.md. A
    # SEPARATE model/concept from `embedding_model` below - generation and
    # embedding are never the same Ollama call or the same model.
    # `llama3.2:3b` is a small, locally-pullable chat model (`ollama pull
    # llama3.2:3b`), not hardcoded into any business logic - only read from
    # here (app/llm/ollama_client.py).
    ollama_llm_model: str = "llama3.2:3b"
    ollama_llm_timeout_seconds: float = 60.0
    # Low temperature/top_p for grounded, factual answers rather than
    # creative variation - never client-configurable (see
    # app/schemas/rag.py, RagAnswerRequest's extra="forbid").
    ollama_llm_temperature: float = 0.1
    ollama_llm_top_p: float = 0.9
    # A fixed seed is passed on every request as a best effort toward
    # reproducibility - NOT a guarantee: Ollama/llama.cpp do not promise
    # bit-for-bit determinism across requests even with a fixed seed (see
    # docs/LLM_GENERATION.md, "Determinism"). Set to empty/unset to omit
    # the seed option entirely.
    ollama_llm_seed: int | None = 42

    # Embeddings. Served locally by Ollama (app/services/documents/embedding.py)
    # rather than a Python ML library - see docs/RAG_INGESTION.md, "Embedding
    # model" for why: this environment's Python (3.14) has no available
    # `torch`/`onnxruntime` wheel, which every Python embedding runtime
    # (including `sentence-transformers`, originally planned here) depends
    # on. `all-minilm` is Ollama's port of the exact same
    # sentence-transformers/all-MiniLM-L6-v2 architecture this project
    # already named, and produces the same 384-dimensional output - `ollama
    # pull all-minilm` before running ingestion locally.
    embedding_model: str = "all-minilm"
    # Must match document_chunks.embedding's pgvector column width exactly
    # (database/migrations/0013_document_chunks.sql) - the embedding service
    # refuses to persist a vector of any other length. Changing the model to
    # one with a different output width requires a migration, not just this
    # setting - see docs/RAG_INGESTION.md, "Embedding model".
    embedding_dimension: int = 384

    # RAG document ingestion (Phase 7). See docs/RAG_INGESTION.md.
    document_storage_path: str = "./data/documents"
    max_document_upload_size_mb: int = 20
    document_chunk_size_chars: int = 1200
    document_chunk_overlap_chars: int = 150

    # RAG hybrid search + reranking (Phase 9). See docs/RAG_HYBRID_SEARCH.md.
    # Candidate-pool multipliers: each retrieval path fetches `top_k *
    # multiplier` candidates before fusion/reranking narrows back down to
    # `top_k` - never just `top_k` from each path merged directly.
    rag_vector_candidate_multiplier: int = 4
    rag_lexical_candidate_multiplier: int = 4
    # Reciprocal Rank Fusion weights/constant - see docs/RAG_HYBRID_SEARCH.md,
    # "Hybrid scoring". Weights need not sum to 1; only their ratio matters.
    rag_vector_weight: float = 0.7
    rag_lexical_weight: float = 0.3
    rag_rrf_k: int = 60  # the constant from the original RRF paper (Cormack et al., 2009)
    # Reranking: a small deterministic boost for query-term overlap in the
    # chunk's own text, added on top of the RRF score - see
    # docs/RAG_HYBRID_SEARCH.md, "Reranking" for why this is a deterministic
    # feature-scoring stage rather than a cross-encoder model.
    rag_rerank_exact_match_bonus: float = 0.05
    # Document-level diversity: at most this many chunks from the same
    # document in one final result set, so one large matching document
    # can't crowd out every other authorized, relevant document.
    rag_max_chunks_per_document: int = 3

    # LLM answer generation, context construction (Phase 10). See
    # docs/LLM_GENERATION.md, "Context limits". Both limits are enforced
    # together - whichever is hit first stops adding sources.
    rag_max_context_chunks: int = 5
    rag_max_context_chars: int = 20000
    # Shown verbatim when authorized retrieval finds nothing relevant -
    # centralized here (not scattered as an inline string) specifically so
    # it's easy to audit for accidental information leakage. Never reveals
    # whether unauthorized documents exist - see docs/LLM_GENERATION.md,
    # "No-context behavior".
    rag_answer_no_context_message: str = (
        "I couldn't find enough information in the available hospital knowledge base to answer that question."
    )

    # Bounded multi-turn conversation support (Phase 13). See
    # docs/CONVERSATIONAL_AUTH_ROUTING.md, "Bounded history"/"Context
    # limits". A "turn" is one user+assistant message pair - never
    # unbounded history, never long-term memory.
    conversation_max_turns: int = 10
    conversation_max_message_chars: int = 4000
    # Bounds the *formatted* <HISTORICAL_CONVERSATION> text sent to the
    # LLM, independent of rag_max_context_chars (which bounds RAG/
    # structured evidence, a separate budget) - whichever of
    # conversation_max_turns/conversation_max_context_chars is hit first
    # stops including older turns.
    conversation_max_context_chars: int = 8000
    # Documented retention policy only (see docs/CONVERSATIONAL_AUTH_ROUTING.md,
    # "Retention") - no background cleanup job exists yet in this project
    # (there is no scheduler/worker infrastructure to hang one off of);
    # deferred rather than built speculatively for this phase.
    conversation_retention_days: int = 30

    # CORS
    cors_allow_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
