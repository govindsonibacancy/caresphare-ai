"""app/core/request_context.py: the RequestIDMiddleware/get_request_id
pair, tested independently of any AI-pipeline behavior - every response
from any endpoint should carry a server-generated X-Request-ID, and a
client can never supply its own. See docs/AUDIT_AND_OBSERVABILITY.md,
"Request/correlation ID"."""

import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_every_response_has_a_request_id_header():
    resp = client.get("/health")
    assert resp.status_code == 200
    uuid.UUID(resp.headers["X-Request-ID"])  # well-formed


def test_request_id_is_unique_across_unrelated_requests():
    first = client.get("/health").headers["X-Request-ID"]
    second = client.get("/health").headers["X-Request-ID"]
    assert first != second


def test_client_supplied_header_is_ignored_even_for_an_unauthenticated_endpoint():
    spoofed = "22222222-2222-2222-2222-222222222222"
    resp = client.get("/health", headers={"X-Request-ID": spoofed})
    assert resp.headers["X-Request-ID"] != spoofed


def test_error_responses_still_carry_a_request_id():
    resp = client.get("/api/authz/me")  # unauthenticated -> 401
    assert resp.status_code in (401, 403)
    uuid.UUID(resp.headers["X-Request-ID"])


def test_x_request_id_is_exposed_to_cross_origin_browser_javascript():
    """Phase 16: without `expose_headers`, a cross-origin frontend's
    `response.headers.get('X-Request-ID')` would always return null, per
    the Fetch spec's CORS-safelisted-response-header-name list - the
    header is still sent, just invisible to JS. See app/main.py and
    docs/FRONTEND_AI_CHAT.md."""
    resp = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert resp.headers.get("access-control-expose-headers") == "X-Request-ID"
