"""app/llm/ollama_client.py: the Ollama chat-completion HTTP boundary,
fully mocked (`httpx.post` never makes a real network call here) so this
file never depends on a live Ollama server - see docs/LLM_GENERATION.md,
"Testing strategy".
"""

import httpx
import pytest

from app.llm.ollama_client import (
    LLMGenerationTimeout,
    LLMMalformedResponse,
    LLMModelNotFound,
    LLMServiceUnavailable,
    OllamaLLMService,
)

MESSAGES = [{"role": "system", "content": "system prompt"}, {"role": "user", "content": "user question"}]


def _service(**overrides) -> OllamaLLMService:
    defaults = dict(
        base_url="http://fake-ollama:11434",
        model="llama3.2:3b",
        timeout_seconds=30.0,
        temperature=0.1,
        top_p=0.9,
        seed=42,
    )
    defaults.update(overrides)
    return OllamaLLMService(**defaults)


class _Response:
    def __init__(self, *, status_code=200, json_body=None, raise_on_json=False):
        self.status_code = status_code
        self.is_error = status_code >= 400
        self._json_body = json_body
        self._raise_on_json = raise_on_json

    def json(self):
        if self._raise_on_json:
            raise ValueError("not valid json")
        return self._json_body


# --- 1. successful generation -----------------------------------------


def test_successful_generation_returns_content(monkeypatch):
    monkeypatch.setattr(
        "httpx.post",
        lambda *a, **k: _Response(json_body={"done": True, "message": {"role": "assistant", "content": "The answer is 4."}}),
    )
    result = _service().generate(messages=MESSAGES)
    assert result == "The answer is 4."


# --- 2. malformed response (missing message) ----------------------------


def test_missing_message_key_is_malformed_response(monkeypatch):
    monkeypatch.setattr("httpx.post", lambda *a, **k: _Response(json_body={"done": True}))
    with pytest.raises(LLMMalformedResponse):
        _service().generate(messages=MESSAGES)


# --- 3. timeout ----------------------------------------------------------


def test_timeout_raises_llm_generation_timeout(monkeypatch):
    def _raise_timeout(*a, **k):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr("httpx.post", _raise_timeout)
    with pytest.raises(LLMGenerationTimeout):
        _service().generate(messages=MESSAGES)


# --- 4. connection failure -------------------------------------------


def test_connection_error_raises_service_unavailable(monkeypatch):
    def _raise_connect_error(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("httpx.post", _raise_connect_error)
    with pytest.raises(LLMServiceUnavailable):
        _service().generate(messages=MESSAGES)


# --- 5. HTTP 4xx (non-404) ------------------------------------------------


def test_http_400_raises_service_unavailable(monkeypatch):
    monkeypatch.setattr("httpx.post", lambda *a, **k: _Response(status_code=400, json_body={"error": "bad request"}))
    with pytest.raises(LLMServiceUnavailable):
        _service().generate(messages=MESSAGES)


# --- 6. HTTP 5xx -----------------------------------------------------


def test_http_500_raises_service_unavailable(monkeypatch):
    monkeypatch.setattr("httpx.post", lambda *a, **k: _Response(status_code=500, json_body={"error": "internal"}))
    with pytest.raises(LLMServiceUnavailable):
        _service().generate(messages=MESSAGES)


# --- 7. model not found (404) ------------------------------------------


def test_model_not_found_raises_llm_model_not_found(monkeypatch):
    monkeypatch.setattr(
        "httpx.post", lambda *a, **k: _Response(status_code=404, json_body={"error": "model 'x' not found"})
    )
    with pytest.raises(LLMModelNotFound):
        _service().generate(messages=MESSAGES)


# --- 8. empty model response ---------------------------------------------


def test_empty_content_is_malformed_response(monkeypatch):
    monkeypatch.setattr(
        "httpx.post", lambda *a, **k: _Response(json_body={"done": True, "message": {"role": "assistant", "content": ""}})
    )
    with pytest.raises(LLMMalformedResponse):
        _service().generate(messages=MESSAGES)


def test_whitespace_only_content_is_malformed_response(monkeypatch):
    monkeypatch.setattr(
        "httpx.post",
        lambda *a, **k: _Response(json_body={"done": True, "message": {"role": "assistant", "content": "   \n  "}}),
    )
    with pytest.raises(LLMMalformedResponse):
        _service().generate(messages=MESSAGES)


# --- 9. unexpected response schema ----------------------------------------


def test_done_false_is_malformed_response(monkeypatch):
    monkeypatch.setattr(
        "httpx.post",
        lambda *a, **k: _Response(json_body={"done": False, "message": {"role": "assistant", "content": "partial"}}),
    )
    with pytest.raises(LLMMalformedResponse):
        _service().generate(messages=MESSAGES)


def test_unparseable_json_is_malformed_response(monkeypatch):
    monkeypatch.setattr("httpx.post", lambda *a, **k: _Response(raise_on_json=True))
    with pytest.raises(LLMMalformedResponse):
        _service().generate(messages=MESSAGES)


def test_non_dict_body_is_malformed_response(monkeypatch):
    monkeypatch.setattr("httpx.post", lambda *a, **k: _Response(json_body=["not", "a", "dict"]))
    with pytest.raises(LLMMalformedResponse):
        _service().generate(messages=MESSAGES)


# --- 10. generation configuration is correctly passed ---------------------


def test_generation_config_is_correctly_passed(monkeypatch):
    captured = {}

    def _fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _Response(json_body={"done": True, "message": {"role": "assistant", "content": "ok"}})

    monkeypatch.setattr("httpx.post", _fake_post)
    service = _service(model="custom-model:1b", temperature=0.3, top_p=0.5, seed=7, timeout_seconds=45.0)
    service.generate(messages=MESSAGES)

    assert captured["url"] == "http://fake-ollama:11434/api/chat"
    assert captured["json"]["model"] == "custom-model:1b"
    assert captured["json"]["messages"] == MESSAGES
    assert captured["json"]["stream"] is False
    assert captured["json"]["options"]["temperature"] == 0.3
    assert captured["json"]["options"]["top_p"] == 0.5
    assert captured["json"]["options"]["seed"] == 7
    assert captured["timeout"] == 45.0


def test_seed_omitted_when_none(monkeypatch):
    captured = {}

    def _fake_post(url, json, timeout):
        captured["json"] = json
        return _Response(json_body={"done": True, "message": {"role": "assistant", "content": "ok"}})

    monkeypatch.setattr("httpx.post", _fake_post)
    _service(seed=None).generate(messages=MESSAGES)
    assert "seed" not in captured["json"]["options"]


def test_base_url_trailing_slash_is_stripped(monkeypatch):
    captured = {}

    def _fake_post(url, json, timeout):
        captured["url"] = url
        return _Response(json_body={"done": True, "message": {"role": "assistant", "content": "ok"}})

    monkeypatch.setattr("httpx.post", _fake_post)
    _service(base_url="http://fake-ollama:11434/").generate(messages=MESSAGES)
    assert captured["url"] == "http://fake-ollama:11434/api/chat"
