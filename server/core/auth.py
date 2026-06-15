from __future__ import annotations

from typing import Optional

import jwt
from jwt import InvalidAudienceError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.auth_types import AuthContext, Principal
from core.config import get_settings
from core.db import get_db
from core.models import TenantMembership


bearer = HTTPBearer(auto_error=False)


def claims_to_principal(claims: dict) -> Principal:
    subject = claims.get("sub")
    if not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token subject is missing")
    return Principal(
        keycloak_subject=subject,
        email=claims.get("email"),
        username=claims.get("preferred_username"),
        display_name=claims.get("name"),
    )


def decode_keycloak_token(token: str) -> dict:
    settings = get_settings()
    jwk_client = PyJWKClient(settings.keycloak_jwks_url)
    signing_key = jwk_client.get_signing_key_from_jwt(token)
    try:
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.keycloak_audience,
            options={"verify_iss": False},
        )
    except InvalidAudienceError:
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_iss": False, "verify_aud": False},
        )
        if claims.get("azp") != settings.keycloak_authorized_party:
            raise
        return claims


def get_auth_context(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: Session = Depends(get_db),
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    try:
        claims = decode_keycloak_token(credentials.credentials)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token") from exc

    principal = claims_to_principal(claims)
    from core.provisioning import provision_user_context

    return provision_user_context(db, principal)


def require_tenant_owner(
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> AuthContext:
    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == ctx.tenant_id,
            TenantMembership.user_id == ctx.user_id,
        )
    )
    if membership is None or membership.role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant owner access required")
    return ctx
