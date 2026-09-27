import logging

logger = logging.getLogger("caresphere.email.development")


class DevelopmentEmailService:
    """Local-dev stand-in for a real email provider: logs the invitation
    instead of sending it, so the invitation flow is exercisable without any
    paid/external service configured.

    This is the ONE place in the codebase allowed to log a URL that contains
    the raw invitation token, and only because there is no other channel to
    hand it to a developer running this locally. A real production
    EmailService implementation (added when this project integrates an
    actual provider) must send the email and must NOT log the token or the
    URL - see backend/app/core/config.py's `environment` setting and
    docs/EMPLOYEE_INVITATIONS.md, "Local development email behavior".
    """

    def send_employee_invitation(
        self,
        *,
        to_email: str,
        first_name: str,
        role: str,
        hospital_name: str,
        invitation_url: str,
    ) -> None:
        logger.info(
            "[DEV EMAIL] Employee invitation for %s <%s> (role=%s, hospital=%s): %s",
            first_name,
            to_email,
            role,
            hospital_name,
            invitation_url,
        )
