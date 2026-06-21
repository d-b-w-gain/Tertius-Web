from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import logging
import secrets
from typing import Optional

import httpx
import jwt
from jwt import InvalidAudienceError
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.auth_types import AuthContext, Principal
from core.config import get_settings
from core.db import get_db
from core.models import AuthSession, TenantMembership


bearer = HTTPBearer(auto_error=False)
REFRESH_SKEW_SECONDS = 30
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
logger = logging.getLogger(__name__)


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
            issuer=settings.keycloak_issuer.rstrip("/"),
        )
    except InvalidAudienceError:
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=settings.keycloak_issuer.rstrip("/"),
            options={"verify_aud": False},
        )
        if claims.get("azp") != settings.keycloak_authorized_party:
            raise
        return claims


def session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(48)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def coerce_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def cookie_max_age(settings) -> int:
    return settings.auth_session_max_seconds


def set_auth_cookies(response: Response, session_token: str, csrf_token: str) -> None:
    settings = get_settings()
    max_age = cookie_max_age(settings)
    response.set_cookie(
        settings.auth_session_cookie_name,
        session_token,
        max_age=max_age,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        settings.auth_csrf_cookie_name,
        csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    for name in (
        settings.auth_session_cookie_name,
        settings.auth_csrf_cookie_name,
        settings.auth_oauth_state_cookie_name,
    ):
        response.delete_cookie(name, path="/")


def require_csrf(request: Request, session: AuthSession) -> None:
    if request.method.upper() in SAFE_METHODS:
        return
    csrf_header = request.headers.get("x-csrf-token")
    if not csrf_header or not hmac.compare_digest(csrf_header, session.csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def refresh_session_access_token(session: AuthSession) -> None:
    settings = get_settings()
    token_url = f"{settings.keycloak_issuer.rstrip('/')}/protocol/openid-connect/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": settings.oidc_client_id,
        "refresh_token": session.refresh_token,
    }
    if settings.oidc_client_secret:
        data["client_secret"] = settings.oidc_client_secret

    try:
        response = httpx.post(token_url, data=data, timeout=10)
    except httpx.RequestError as exc:
        logger.warning("Keycloak token refresh request failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from exc

    if response.status_code >= 500:
        logger.warning("Keycloak token refresh returned %s", response.status_code)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        )

    if response.status_code >= 400:
        logger.info("Keycloak token refresh rejected stored session with status %s", response.status_code)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication expired")

    try:
        payload = response.json()
    except ValueError as exc:
        logger.warning("Keycloak token refresh returned invalid JSON")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from exc

    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    expires_in = int(payload.get("expires_in") or 300)
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication expired")

    session.access_token = access_token
    if refresh_token:
        session.refresh_token = refresh_token
    session.access_token_expires_at = utc_now() + timedelta(seconds=expires_in)
    session.updated_at = utc_now()


def get_cookie_auth_context(request: Request, response: Response, db: Session) -> AuthContext | None:
    settings = get_settings()
    session_token = request.cookies.get(settings.auth_session_cookie_name)
    if not session_token:
        return None

    session = db.scalar(select(AuthSession).where(AuthSession.session_token_hash == session_token_hash(session_token)))
    if session is None:
        logger.info("Authentication cookie did not match any stored session")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    now = utc_now()
    if coerce_aware(session.idle_expires_at) <= now or coerce_aware(session.max_expires_at) <= now:
        logger.info("Stored auth session expired")
        db.delete(session)
        db.commit()
        clear_auth_cookies(response)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    require_csrf(request, session)

    if coerce_aware(session.access_token_expires_at) <= now + timedelta(seconds=REFRESH_SKEW_SECONDS):
        try:
            refresh_session_access_token(session)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_401_UNAUTHORIZED:
                db.delete(session)
                db.commit()
                clear_auth_cookies(response)
            raise

    principal = claims_to_principal(decode_keycloak_token(session.access_token))
    from core.provisioning import provision_user_context

    ctx = provision_user_context(db, principal)
    session.user_id = ctx.user_id
    session.tenant_id = ctx.tenant_id
    session.keycloak_subject = ctx.keycloak_subject
    session.email = ctx.email
    session.username = principal.username
    session.display_name = principal.display_name
    session.idle_expires_at = min(
        now + timedelta(seconds=settings.auth_session_idle_seconds),
        coerce_aware(session.max_expires_at),
    )
    session.updated_at = now
    db.commit()
    set_auth_cookies(response, session_token, session.csrf_token)
    return ctx


def get_auth_context(
    request: Request,
    response: Response,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: Session = Depends(get_db),
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        cookie_ctx = get_cookie_auth_context(request, response, db)
        if cookie_ctx is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authentication")
        return cookie_ctx

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
