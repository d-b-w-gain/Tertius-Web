from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Principal:
    keycloak_subject: str
    email: str | None
    username: str | None
    display_name: str | None


@dataclass(frozen=True)
class AuthContext:
    user_id: UUID
    tenant_id: UUID
    keycloak_subject: str
    email: str | None
