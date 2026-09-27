"""Exercises real signature/issuer/audience/expiration checks (not the
FastAPI plumbing - see test_auth_me.py for that) using a throwaway RSA key
pair, so this never needs network access to a real Supabase project.
"""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth.jwt_verifier import SupabaseJWKSVerifier, TokenVerificationError

ISSUER = "https://example.supabase.co/auth/v1"
AUDIENCE = "authenticated"


@pytest.fixture(scope="module")
def keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _make_token(
    private_key,
    *,
    issuer=ISSUER,
    audience=AUDIENCE,
    exp_delta=3600,
    sub="00000000-0000-0000-0000-000000000001",
):
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": "demo@example.test",
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + exp_delta,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def _verifier(public_key):
    return SupabaseJWKSVerifier(issuer=ISSUER, audience=AUDIENCE, signing_key_resolver=lambda token: public_key)


def test_valid_token_is_accepted(keypair):
    private_key, public_key = keypair
    claims = _verifier(public_key).verify(_make_token(private_key))
    assert claims.sub == "00000000-0000-0000-0000-000000000001"
    assert claims.email == "demo@example.test"


def test_wrong_signature_is_rejected(keypair):
    _, public_key = keypair
    other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(TokenVerificationError):
        _verifier(public_key).verify(_make_token(other_private_key))


def test_wrong_issuer_is_rejected(keypair):
    private_key, public_key = keypair
    with pytest.raises(TokenVerificationError):
        _verifier(public_key).verify(_make_token(private_key, issuer="https://evil.example/auth/v1"))


def test_wrong_audience_is_rejected(keypair):
    private_key, public_key = keypair
    with pytest.raises(TokenVerificationError):
        _verifier(public_key).verify(_make_token(private_key, audience="not-authenticated"))


def test_expired_token_is_rejected(keypair):
    private_key, public_key = keypair
    with pytest.raises(TokenVerificationError):
        _verifier(public_key).verify(_make_token(private_key, exp_delta=-10))


def test_unsigned_token_is_rejected(keypair):
    _, public_key = keypair
    now = int(time.time())
    unsigned = jwt.encode(
        {"sub": "x", "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + 3600},
        key="",
        algorithm="none",
    )
    with pytest.raises(TokenVerificationError):
        _verifier(public_key).verify(unsigned)
