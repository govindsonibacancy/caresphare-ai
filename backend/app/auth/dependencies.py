from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.jwt_verifier import TokenClaims, TokenVerificationError, TokenVerifier, get_token_verifier
from app.core.db import get_db
from app.repositories import user_repository
from app.schemas.auth import AuthenticatedUser

_bearer_scheme = HTTPBearer(auto_error=False)


def get_verified_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    verifier: TokenVerifier = Depends(get_token_verifier),
) -> TokenClaims:
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return verifier.verify(credentials.credentials)
    except TokenVerificationError as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_current_auth_user(
    claims: TokenClaims = Depends(get_verified_claims),
    db: Session = Depends(get_db),
) -> AuthenticatedUser:
    """Resolves a verified Supabase identity to the CareSphere application
    user. Only checks that the identity is provisioned and active - it makes
    no permission/role decisions (that's Phase 4).
    """
    record = user_repository.get_user_by_auth_id(db, claims.sub)
    if record is None:
        raise HTTPException(
            status_code=403,
            detail="Authenticated identity is not provisioned in CareSphere.",
        )
    if not record.is_active:
        raise HTTPException(status_code=403, detail="This account is inactive.")
    return AuthenticatedUser(**record.__dict__)
