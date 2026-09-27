"""Embedding generation. See docs/RAG_INGESTION.md, "Embedding model" for
why this calls Ollama's local `/api/embed` rather than loading a Python ML
library directly: this project's Python (3.14) has no available `torch`/
`onnxruntime` wheel yet, which every Python-side embedding runtime needs.
Ollama is already this project's designated local model runtime (Phase 10
will use it for the LLM too) - `all-minilm` is its port of the exact
sentence-transformers/all-MiniLM-L6-v2 architecture this project always
named, producing the same 384-dimensional output via a different process
rather than a different model.
"""

from typing import Protocol

import httpx

from app.core.config import get_settings


class EmbeddingServiceError(Exception):
    """Wraps an unreachable/erroring embedding provider into a safe
    message - never a raw connection error or stack trace."""


class EmbeddingDimensionMismatch(Exception):
    """The provider returned a vector of the wrong length. Never insert a
    wrongly-shaped vector into pgvector - the ingestion pipeline treats this
    exactly like any other processing failure (document -> FAILED)."""


class EmbeddingService(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbeddingService:
    def __init__(self, *, base_url: str, model: str, expected_dimension: int) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._expected_dimension = expected_dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = httpx.post(
                f"{self._base_url}/api/embed",
                json={"model": self._model, "input": texts},
                timeout=120.0,
            )
        except httpx.HTTPError as exc:
            raise EmbeddingServiceError(f"Could not reach the embedding service: {exc}") from exc

        if response.is_error:
            raise EmbeddingServiceError(f"Embedding service returned an error ({response.status_code}).")

        body = response.json()
        embeddings = body.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise EmbeddingServiceError("Embedding service returned an unexpected response shape.")

        for vector in embeddings:
            if len(vector) != self._expected_dimension:
                raise EmbeddingDimensionMismatch(
                    f"Expected {self._expected_dimension}-dimensional embeddings, got {len(vector)}."
                )
        return embeddings


_service: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    global _service
    if _service is None:
        settings = get_settings()
        _service = OllamaEmbeddingService(
            base_url=settings.ollama_base_url,
            model=settings.embedding_model,
            expected_dimension=settings.embedding_dimension,
        )
    return _service
