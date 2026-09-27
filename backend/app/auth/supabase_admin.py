from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import httpx

from app.core.config import get_settings


@dataclass(frozen=True)
class SupabaseUser:
    id: str
    email: str
    email_confirmed_at: str | None


class SupabaseAdminError(Exception):
    """Raised when the Supabase Admin API can't be reached or errors."""


class SupabaseAdminClient(Protocol):
    def get_user_by_id(self, auth_user_id: str) -> SupabaseUser | None: ...


class HttpSupabaseAdminClient:
    """Calls Supabase's GoTrue Admin API with the service-role key.

    This is the only place this backend uses the service-role key, and only
    to confirm a just-registered identity really exists in Supabase before
    creating the matching application user - the key itself is read from
    backend-only settings and never returned to a client.
    """

    def __init__(self, *, base_url: str, service_role_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_role_key = service_role_key

    def get_user_by_id(self, auth_user_id: str) -> SupabaseUser | None:
        if not self._service_role_key or not self._base_url:
            raise SupabaseAdminError(
                "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not configured; "
                "patient registration cannot verify identities."
            )
        try:
            response = httpx.get(
                f"{self._base_url}/auth/v1/admin/users/{auth_user_id}",
                headers={
                    "apikey": self._service_role_key,
                    "Authorization": f"Bearer {self._service_role_key}",
                },
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            raise SupabaseAdminError(f"Supabase admin API request failed: {exc}") from exc

        if response.status_code == 404:
            return None
        if response.is_error:
            raise SupabaseAdminError(f"Supabase admin API error: {response.status_code}")

        body = response.json()
        return SupabaseUser(
            id=body["id"],
            email=body["email"],
            email_confirmed_at=body.get("email_confirmed_at"),
        )


@lru_cache
def get_supabase_admin_client() -> SupabaseAdminClient:
    settings = get_settings()
    return HttpSupabaseAdminClient(
        base_url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
    )
