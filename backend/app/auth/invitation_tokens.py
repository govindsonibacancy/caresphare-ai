import hashlib
import secrets

# 32 random bytes (256 bits) - not a UUID, not a timestamp, not anything
# derived from the invitee's identity. secrets.token_urlsafe is a CSPRNG
# (os.urandom under the hood), sized so brute-forcing the token space is
# infeasible.
_TOKEN_BYTES = 32


def generate_invitation_token() -> str:
    """The raw, one-time credential that goes in the invitation URL. Never
    stored - only hash_invitation_token()'s output is persisted."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_invitation_token(token: str) -> str:
    """SHA-256 of the raw token, stored in employee_invitations.invitation_
    token_hash. Acceptance looks a presented token up by this hash (an
    indexed equality lookup), which is why no manual constant-time string
    comparison is needed here: the "comparison" is a database index match on
    a fixed-length hash, not a byte-by-byte comparison of secret material in
    application code.

    SHA-256 (not bcrypt/scrypt/argon2) is appropriate here specifically
    because the input is already a uniformly random 256-bit token, not a
    low-entropy human-chosen password - there is no dictionary/brute-force
    concern a slow KDF would meaningfully mitigate that the token's own
    entropy doesn't already provide.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
