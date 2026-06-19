import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import timedelta
import hashlib
import hmac
import json
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy import select

# Ensure the workflows directory is in the Python path so we can import them
sys.path.append(str(Path(__file__).parent))

from core.auth import (
    AuthContext,
    claims_to_principal,
    clear_auth_cookies,
    decode_keycloak_token,
    get_auth_context,
    new_csrf_token,
    new_session_token,
    session_token_hash,
    set_auth_cookies,
    utc_now,
)
from core.config import get_settings
from core.db import get_db
from core.models import AuthSession

# Import the individual FastAPI apps
from workflows.intus.intus_server import app as intus_app
from workflows.artus.artus_server import app as artus_app
from workflows.extus.extus_server import app as extus_app
from workflows.timus.timus_server import app as timus_app
from workflows.intus.compile_result_consumer import run_result_consumer
from core.provisioning import provision_user_context

settings = get_settings()
_compile_result_stop_event: asyncio.Event | None = None
_compile_result_task: asyncio.Task | None = None


async def start_compile_result_consumer():
    global _compile_result_stop_event, _compile_result_task
    _compile_result_stop_event = asyncio.Event()
    _compile_result_task = asyncio.create_task(run_result_consumer(_compile_result_stop_event))


async def stop_compile_result_consumer():
    if _compile_result_stop_event is not None:
        _compile_result_stop_event.set()
    if _compile_result_task is not None:
        _compile_result_task.cancel()
        try:
            await _compile_result_task
        except asyncio.CancelledError:
            pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await start_compile_result_consumer()
    try:
        yield
    finally:
        await stop_compile_result_consumer()


# Create the master Monolith app
app = FastAPI(title="Tertius Monolith API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Tertius Backend is running"}


@app.get("/api/me")
def read_me(ctx: AuthContext = Depends(get_auth_context)):
    return {
        "user_id": str(ctx.user_id),
        "tenant_id": str(ctx.tenant_id),
        "email": ctx.email,
    }


def _auth_state_secret() -> bytes:
    secret = settings.auth_session_secret or settings.oidc_client_secret
    if not secret:
        if not settings.auth_allow_insecure_oauth_state_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication is not configured: set AUTH_SESSION_SECRET or OIDC_CLIENT_SECRET.",
            )
        secret = "insecure-local-auth-state-secret"
    return secret.encode("utf-8")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64url(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(f"{data}{padding}".encode("ascii"))


def _sign_payload(payload: dict) -> str:
    raw = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_auth_state_secret(), raw.encode("ascii"), hashlib.sha256).digest()
    return f"{raw}.{_b64url(signature)}"


def _unsign_payload(value: str) -> dict:
    try:
        raw, signature = value.split(".", 1)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth state") from exc
    expected = _b64url(hmac.new(_auth_state_secret(), raw.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth state")
    return json.loads(_unb64url(raw))


def _external_url(request: Request, path: str) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}{path}"


def _safe_return_to(return_to: str) -> str:
    if not return_to.startswith("/") or return_to.startswith("//"):
        return "/"
    return return_to


def _code_challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


@app.get("/api/auth/login")
def auth_login(
    request: Request,
    return_to: str = Query(default="/"),
):
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    redirect_uri = _external_url(request, "/api/auth/callback")
    state_cookie = _sign_payload(
        {
            "state": state,
            "verifier": verifier,
            "return_to": _safe_return_to(return_to),
            "iat": int(utc_now().timestamp()),
        }
    )
    params = urlencode(
        {
            "client_id": settings.oidc_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid profile email",
            "state": state,
            "code_challenge": _code_challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    response = RedirectResponse(f"{settings.keycloak_issuer.rstrip('/')}/protocol/openid-connect/auth?{params}")
    response.set_cookie(
        settings.auth_oauth_state_cookie_name,
        state_cookie,
        max_age=600,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/api/auth/callback")
def auth_callback(
    request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    db=Depends(get_db),
):
    stored = request.cookies.get(settings.auth_oauth_state_cookie_name)
    if not stored:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing OAuth state")
    payload = _unsign_payload(stored)
    if payload.get("state") != state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth state")
    if int(payload.get("iat", 0)) < int((utc_now() - timedelta(minutes=10)).timestamp()):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Expired OAuth state")

    redirect_uri = _external_url(request, "/api/auth/callback")
    token_data = {
        "grant_type": "authorization_code",
        "client_id": settings.oidc_client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": payload["verifier"],
    }
    if settings.oidc_client_secret:
        token_data["client_secret"] = settings.oidc_client_secret

    token_response = httpx.post(
        f"{settings.keycloak_issuer.rstrip('/')}/protocol/openid-connect/token",
        data=token_data,
        timeout=10,
    )
    if token_response.status_code >= 400:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OIDC token exchange failed")
    token_payload = token_response.json()
    access_token = token_payload.get("access_token")
    refresh_token = token_payload.get("refresh_token")
    expires_in = int(token_payload.get("expires_in") or 300)
    if not access_token or not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OIDC token response was incomplete")

    claims = decode_keycloak_token(access_token)
    principal = claims_to_principal(claims)
    ctx = provision_user_context(db, principal)

    session_token = new_session_token()
    csrf_token = new_csrf_token()
    now = utc_now()
    session = AuthSession(
        session_token_hash=session_token_hash(session_token),
        user_id=ctx.user_id,
        tenant_id=ctx.tenant_id,
        keycloak_subject=ctx.keycloak_subject,
        email=ctx.email,
        username=principal.username,
        display_name=principal.display_name,
        access_token=access_token,
        refresh_token=refresh_token,
        csrf_token=csrf_token,
        access_token_expires_at=now + timedelta(seconds=expires_in),
        idle_expires_at=now + timedelta(seconds=settings.auth_session_idle_seconds),
        max_expires_at=now + timedelta(seconds=settings.auth_session_max_seconds),
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()

    response = RedirectResponse(_safe_return_to(str(payload.get("return_to") or "/")))
    response.delete_cookie(settings.auth_oauth_state_cookie_name, path="/")
    set_auth_cookies(response, session_token, csrf_token)
    return response


@app.get("/api/auth/me")
def auth_me(ctx: AuthContext = Depends(get_auth_context)):
    return {
        "authenticated": True,
        "user_id": str(ctx.user_id),
        "tenant_id": str(ctx.tenant_id),
        "email": ctx.email,
    }


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response, db=Depends(get_db)):
    session_token = request.cookies.get(settings.auth_session_cookie_name)
    if session_token:
        session = db.scalar(select(AuthSession).where(AuthSession.session_token_hash == session_token_hash(session_token)))
        if session is not None:
            db.delete(session)
            db.commit()
    clear_auth_cookies(response)
    return {"ok": True}


# Mount the workflows to sub-paths
app.mount("/api/intus", intus_app)
app.mount("/api/artus", artus_app)
app.mount("/api/extus", extus_app)
app.mount("/api/timus", timus_app)

if __name__ == "__main__":
    import uvicorn
    # Use environment variable for port, default to 8000
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
