from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(default="postgresql+psycopg://tertius:tertius@localhost:5432/tertius")
    keycloak_issuer: str = Field(default="http://localhost:8080/realms/tertius")
    keycloak_audience: str = Field(default="tertius-web")
    artifact_root: str = Field(default="/tmp/tertius-artifacts")
    allowed_origins: str = Field(default="http://localhost:5173")

    @property
    def keycloak_jwks_url(self) -> str:
        return f"{self.keycloak_issuer.rstrip('/')}/protocol/openid-connect/certs"

    @property
    def allowed_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
