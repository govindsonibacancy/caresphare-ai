"""Ollama chat-completion client - the generation-model counterpart to
app/services/documents/embedding.py's `OllamaEmbeddingService`. Same
project, same local Ollama runtime, a **separate model**: embeddings use
`all-minilm` (384 dimensions); generation uses a configurable chat model
(default `llama3.2:3b` - already pulled locally, see
docs/LLM_GENERATION.md, "Model configuration"). Nothing in this module
touches the embedding model or vice versa.

This is the entire HTTP boundary to Ollama for answer generation - no
other module in this codebase makes an `/api/chat` call. Callers get
`messages`/`content` in, `content: str` out, never a raw `httpx.Response`.
"""

from typing import Protocol

import httpx

from app.core.config import get_settings


class LLMServiceError(Exception):
    """Base for every LLM-generation failure. `str(exc)` is always safe to
    show a caller directly - never a raw connection string, host, or port
    (see docs/LLM_GENERATION.md, "Error handling")."""


class LLMServiceUnavailable(LLMServiceError):
    """Ollama itself could not be reached (connection refused, DNS
    failure, non-timeout network error) or returned a non-404 error
    status."""


class LLMModelNotFound(LLMServiceError):
    """The configured model is not pulled/available in this Ollama
    instance - a configuration problem, not a transient outage."""


class LLMGenerationTimeout(LLMServiceError):
    """Generation did not complete within `ollama_llm_timeout_seconds`."""


class LLMMalformedResponse(LLMServiceError):
    """Ollama responded, but not with the expected shape - missing
    `message.content`, `done` was false, or the body wasn't valid JSON.
    Never leaks the raw body (could contain unrelated internal detail)."""


class LLMService(Protocol):
    def generate(self, *, messages: list[dict[str, str]]) -> str: ...


class OllamaLLMService:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float,
        temperature: float,
        top_p: float,
        seed: int | None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._temperature = temperature
        self._top_p = top_p
        self._seed = seed

    def generate(self, *, messages: list[dict[str, str]]) -> str:
        """`messages` is a plain `[{"role": "system"|"user", "content": ...}]`
        list - the caller (app/llm/prompts.py) is responsible for prompt
        structure and injection defense; this method only speaks Ollama's
        `/api/chat` protocol. Generation parameters (temperature/top_p/seed)
        are always the server-configured ones - there is no parameter here
        for a caller to override them, so a client can never influence
        model choice or generation behavior (see docs/LLM_GENERATION.md,
        "Generation parameters").
        """
        options: dict[str, object] = {"temperature": self._temperature, "top_p": self._top_p}
        if self._seed is not None:
            options["seed"] = self._seed

        try:
            response = httpx.post(
                f"{self._base_url}/api/chat",
                json={"model": self._model, "messages": messages, "stream": False, "options": options},
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise LLMGenerationTimeout("The language model did not respond in time.") from exc
        except httpx.HTTPError as exc:
            raise LLMServiceUnavailable("Could not reach the language model service.") from exc

        if response.status_code == 404:
            raise LLMModelNotFound("The configured language model is not available.")
        if response.is_error:
            raise LLMServiceUnavailable(f"The language model service returned an error ({response.status_code}).")

        try:
            body = response.json()
        except ValueError as exc:
            raise LLMMalformedResponse("The language model service returned an unreadable response.") from exc

        if not isinstance(body, dict) or body.get("done") is not True:
            raise LLMMalformedResponse("The language model did not finish generating a response.")

        message = body.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise LLMMalformedResponse("The language model returned an empty response.")

        return content


_service: LLMService | None = None


def get_llm_service() -> LLMService:
    global _service
    if _service is None:
        settings = get_settings()
        _service = OllamaLLMService(
            base_url=settings.ollama_base_url,
            model=settings.ollama_llm_model,
            timeout_seconds=settings.ollama_llm_timeout_seconds,
            temperature=settings.ollama_llm_temperature,
            top_p=settings.ollama_llm_top_p,
            seed=settings.ollama_llm_seed,
        )
    return _service
