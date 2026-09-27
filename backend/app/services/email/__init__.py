from functools import lru_cache

from app.services.email.base import EmailService
from app.services.email.development import DevelopmentEmailService

__all__ = ["EmailService", "get_email_service"]


@lru_cache
def get_email_service() -> EmailService:
    """No real email provider is integrated yet (see
    docs/EMPLOYEE_INVITATIONS.md, "Local development email behavior").
    Always returns the development implementation for now; adding a real
    provider means adding an implementation of EmailService and switching
    this factory on `get_settings().environment`, not changing any caller.
    """
    return DevelopmentEmailService()
