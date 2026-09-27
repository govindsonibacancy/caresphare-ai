from typing import Protocol


class EmailService(Protocol):
    def send_employee_invitation(
        self,
        *,
        to_email: str,
        first_name: str,
        role: str,
        hospital_name: str,
        invitation_url: str,
    ) -> None: ...
