from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import jwt
from jwt import PyJWKClient

from app.core.config import get_settings


class TokenVerificationError(Exception):
    """Raised for any signature/issuer/audience/expiration/claims failure."""


@dataclass(frozen=True)
class TokenClaims:
    sub: str
    email: str | None
    raw: dict


class TokenVerifier(Protocol):
    def verify(self, token: str) -> TokenClaims: ...


class SupabaseJWKSVerifier:
    """Verifies a Supabase-issued access token against the project's JWKS.

    Checks signature, issuer, audience, and expiration - this never trusts a
    decoded-but-unverified payload. `signing_key_resolver` is injected so
    tests can supply a known key pair instead of fetching JWKS over the
    network (see backend/tests/test_jwt_verifier.py).
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        signing_key_resolver: Callable[[str], object],
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._signing_key_resolver = signing_key_resolver

    def verify(self, token: str) -> TokenClaims:
        try:
            signing_key = self._signing_key_resolver(token)
            key = signing_key.key if hasattr(signing_key, "key") else signing_key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256", "ES256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise TokenVerificationError(str(exc)) from exc
        return TokenClaims(sub=claims["sub"], email=claims.get("email"), raw=claims)


@lru_cache
def get_token_verifier() -> TokenVerifier:
    """Cached as a FastAPI dependency, so this runs on every request to a
    protected endpoint - including ones with no bearer token at all (a
    missing token is rejected before .verify() is ever called, but the
    dependency itself is still constructed). PyJWKClient validates its URL
    eagerly at construction time, so building it here (rather than lazily,
    inside the resolver, on first actual use) would make every request -
    even a token-less one - fail hard whenever SUPABASE_URL isn't configured.
    """
    settings = get_settings()
    issuer = settings.supabase_jwt_issuer or f"{settings.supabase_url}/auth/v1"

    def resolve_signing_key(token: str) -> object:
        jwks_url = f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"
        return PyJWKClient(jwks_url).get_signing_key_from_jwt(token)

    return SupabaseJWKSVerifier(
        issuer=issuer,
        audience=settings.supabase_jwt_audience,
        signing_key_resolver=resolve_signing_key,
    )
