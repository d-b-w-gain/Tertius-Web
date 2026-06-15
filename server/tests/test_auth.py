from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import core.auth as auth
from core.auth import claims_to_principal, decode_keycloak_token, get_auth_context
from core.config import Settings


def _private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _token(private_key, **overrides):
    payload = {
        "sub": "kc-user-1",
        "iss": "http://issuer.example/realms/tertius",
        "aud": "tertius-api",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }
    payload.update(overrides)
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "test-key"})


class _SigningKey:
    def __init__(self, key):
        self.key = key


class _JwkClient:
    public_key = None

    def __init__(self, url: str):
        self.url = url

    def get_signing_key_from_jwt(self, token: str):
        return _SigningKey(self.public_key)


class _FailingJwkClient:
    def __init__(self, url: str):
        self.url = url

    def get_signing_key_from_jwt(self, token: str):
        raise Exception("unknown kid")


def _patch_settings(monkeypatch):
    settings = Settings(
        database_url="postgresql+psycopg://tertius:tertius@localhost:5432/tertius",
        keycloak_issuer="http://issuer.example/realms/tertius",
        keycloak_audience="tertius-api",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)


def test_claims_to_principal_requires_subject():
    with pytest.raises(HTTPException) as exc:
        claims_to_principal({"email": "a@example.com"})

    assert exc.value.status_code == 401


def test_claims_to_principal_maps_keycloak_claims():
    principal = claims_to_principal(
        {
            "sub": "kc-user-1",
            "email": "a@example.com",
            "preferred_username": "alice",
            "name": "Alice Example",
        }
    )

    assert principal.keycloak_subject == "kc-user-1"
    assert principal.email == "a@example.com"
    assert principal.username == "alice"
    assert principal.display_name == "Alice Example"


def test_decode_keycloak_token_accepts_valid_rs256_token(monkeypatch):
    key = _private_key()
    _JwkClient.public_key = key.public_key()
    monkeypatch.setattr(auth, "PyJWKClient", _JwkClient)
    _patch_settings(monkeypatch)

    claims = decode_keycloak_token(_token(key))

    assert claims["sub"] == "kc-user-1"


def test_decode_keycloak_token_accepts_trusted_ui_authorized_party(monkeypatch):
    key = _private_key()
    _JwkClient.public_key = key.public_key()
    monkeypatch.setattr(auth, "PyJWKClient", _JwkClient)
    _patch_settings(monkeypatch)

    claims = decode_keycloak_token(_token(key, aud="account", azp="tertius-ui"))

    assert claims["sub"] == "kc-user-1"


def test_decode_keycloak_token_rejects_wrong_audience(monkeypatch):
    key = _private_key()
    _JwkClient.public_key = key.public_key()
    monkeypatch.setattr(auth, "PyJWKClient", _JwkClient)
    _patch_settings(monkeypatch)

    with pytest.raises(jwt.InvalidAudienceError):
        decode_keycloak_token(_token(key, aud="wrong-audience"))


def test_decode_keycloak_token_rejects_wrong_issuer(monkeypatch):
    key = _private_key()
    _JwkClient.public_key = key.public_key()
    monkeypatch.setattr(auth, "PyJWKClient", _JwkClient)
    _patch_settings(monkeypatch)

    with pytest.raises(jwt.InvalidIssuerError):
        decode_keycloak_token(_token(key, iss="http://attacker.example/realms/tertius"))


def test_decode_keycloak_token_rejects_untrusted_authorized_party(monkeypatch):
    key = _private_key()
    _JwkClient.public_key = key.public_key()
    monkeypatch.setattr(auth, "PyJWKClient", _JwkClient)
    _patch_settings(monkeypatch)

    with pytest.raises(jwt.InvalidAudienceError):
        decode_keycloak_token(_token(key, aud="account", azp="other-client"))


def test_decode_keycloak_token_rejects_expired_token(monkeypatch):
    key = _private_key()
    _JwkClient.public_key = key.public_key()
    monkeypatch.setattr(auth, "PyJWKClient", _JwkClient)
    _patch_settings(monkeypatch)

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_keycloak_token(_token(key, exp=datetime.now(timezone.utc) - timedelta(minutes=1)))


def test_decode_keycloak_token_rejects_unknown_kid(monkeypatch):
    key = _private_key()
    monkeypatch.setattr(auth, "PyJWKClient", _FailingJwkClient)
    _patch_settings(monkeypatch)

    with pytest.raises(Exception, match="unknown kid"):
        decode_keycloak_token(_token(key))


def test_get_auth_context_rejects_missing_bearer_token():
    with pytest.raises(HTTPException) as exc:
        get_auth_context(credentials=None, db=None)

    assert exc.value.status_code == 401


def test_get_auth_context_rejects_malformed_token():
    with pytest.raises(HTTPException) as exc:
        get_auth_context(credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-jwt"), db=None)

    assert exc.value.status_code == 401
