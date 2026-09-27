class PermissionDenied(Exception):
    """Raised by the authorization layer (has_permission/authorize/can_access_*).

    Deliberately not a FastAPI HTTPException: these functions are plain,
    independently unit-testable Python and shouldn't depend on the web
    framework. The FastAPI dependency layer (app/permissions/dependencies.py)
    is the one place this gets translated to a 403 response - see
    docs/AUTHORIZATION.md, "401 vs 403".

    `reason` is for logs/tests, never for the HTTP response body: what a
    caller was denied and why is not information to hand back to them (see
    docs/AUTHORIZATION.md, "what a 403 does and doesn't reveal").
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
