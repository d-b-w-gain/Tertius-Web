from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


SERVER_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def settings_config() -> SettingsConfigDict:
    return SettingsConfigDict(env_file=SERVER_ENV_FILE, env_file_encoding="utf-8", extra="ignore")


class Settings(BaseSettings):
    model_config = settings_config()

    database_url: str = Field(default="")
    app_db_host: str = Field(default="")
    app_db_name: str = Field(default="tertius")
    app_db_owner: str = Field(default="tertius")
    app_db_password: str = Field(default="")
    keycloak_issuer: str = Field(default="http://localhost:8080/realms/tertius")
    keycloak_audience: str = Field(default="tertius-web")
    keycloak_authorized_party: str = Field(default="tertius-ui")
    keycloak_jwks_url_override: str | None = Field(default=None)
    artifact_root: str = Field(default="/tmp/tertius-artifacts")
    artifact_retention_limit: int = Field(default=10)
    allowed_origins: str = Field(default="http://localhost:5173")

    @model_validator(mode="after")
    def populate_database_url(self):
        if self.database_url:
            return self
        if self.app_db_host and self.app_db_name and self.app_db_owner and self.app_db_password:
            username = quote_plus(self.app_db_owner)
            password = quote_plus(self.app_db_password)
            host = self.app_db_host
            database = quote_plus(self.app_db_name)
            self.database_url = f"postgresql+psycopg://{username}:{password}@{host}:5432/{database}"
        else:
            self.database_url = "postgresql+psycopg://tertius:tertius@localhost:5432/tertius"
        return self

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
