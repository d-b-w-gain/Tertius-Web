"""Public read-only project share links (#417).

A link is ``/share#<token>``. The token never reaches a URL path: the share
page sends it in ``SHARE_TOKEN_HEADER``, so it stays out of proxy and access
logs. A token is ``<share id hex>.<HMAC-SHA256(key, share id)>``: nothing
secret is stored, and the owner can copy a link again at any time. The key is
derived from the session secret, so rotating that secret invalidates every
link.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from uuid import UUID

SHARE_TOKEN_HEADER = "X-Tertius-Share-Token"
EXTUS_WORKBENCH = "extus"
_KEY_LABEL = b"tertius-share-link-v1"
_MAX_TOKEN_LENGTH = 128


class ShareLinksUnavailable(RuntimeError):
    """No secret is configured to sign share links with."""


def _key(settings) -> bytes:
    secret = settings.auth_session_secret or settings.oidc_client_secret
    if not secret:
        if not getattr(settings, "auth_allow_insecure_oauth_state_secret", False):
            raise ShareLinksUnavailable("set AUTH_SESSION_SECRET to sign share links")
        secret = "insecure-local-share-link-secret"
    return hmac.new(secret.encode("utf-8"), _KEY_LABEL, hashlib.sha256).digest()


def _signature(share_id: UUID, settings) -> str:
    digest = hmac.new(_key(settings), share_id.bytes, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def share_token(share_id: UUID, settings) -> str:
    return f"{share_id.hex}.{_signature(share_id, settings)}"


def share_id_from_token(token: str | None, settings) -> UUID | None:
    """The share id a token was signed for, or None for anything else."""
    if not token or len(token) > _MAX_TOKEN_LENGTH or token.count(".") != 1:
        return None
    id_hex, signature = token.split(".")
    try:
        share_id = UUID(hex=id_hex)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _signature(share_id, settings)):
        return None
    return share_id
