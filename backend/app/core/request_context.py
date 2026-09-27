"""Server-generated request correlation id - see
docs/AUDIT_AND_OBSERVABILITY.md, "Request/correlation ID".

Always generated here, never accepted from a client-supplied header. A
request id is a correlation value for support/debugging/audit
correlation, never an authorization mechanism - accepting an inbound one
would let a client inject an arbitrary value into every audit row this
request produces, for no real benefit in a project with no existing
distributed-tracing infrastructure to interoperate with.
"""

import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.request_id = uuid.uuid4()
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = str(request.state.request_id)
        return response


def get_request_id(request: Request) -> uuid.UUID:
    """FastAPI dependency - reads the id `RequestIDMiddleware` already
    generated for this request. Never a second id, never client-supplied,
    never used for anything beyond correlation/audit metadata."""
    return request.state.request_id
