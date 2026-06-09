from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


SERVER_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def settings_config() -> SettingsConfigDict:
    return SettingsConfigDict(env_file=SERVER_ENV_FILE, env_file_encoding="utf-8", extra="ignore")


class Settings(BaseSettings):
    model_config = settings_config()

    database_url: str = Field(default="postgresql+psycopg://tertius:tertius@localhost:5432/tertius")
    keycloak_issuer: str = Field(default="http://localhost:8080/realms/tertius")
    keycloak_audience: str = Field(default="tertius-web")
    keycloak_jwks_url_override: str | None = Field(default=None)
    artifact_root: str = Field(default="/tmp/tertius-artifacts")
    artifact_retention_limit: int = Field(default=10)
    allowed_origins: str = Field(default="http://localhost:5173")

    @property
    def keycloak_jwks_url(self) -> str:
        if self.keycloak_jwks_url_override:
            return self.keycloak_jwks_url_override
        return f"{self.keycloak_issuer.rstrip('/')}/protocol/openid-connect/certs"

    @property
    def allowed_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
