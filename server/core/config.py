from functools import lru_cache
import json
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def settings_config() -> SettingsConfigDict:
    return SettingsConfigDict(env_file=SERVER_ENV_FILE, env_file_encoding="utf-8", extra="ignore")


class LlmModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    label: str = Field(default="", max_length=120)
    model: str = Field(default="", max_length=200)
    endpoint: str = Field(min_length=1, max_length=500)
    api: Literal["openai-chat-completions", "anthropic-messages"] = "openai-chat-completions"
    input_price_per_million: float = Field(ge=0)
    output_price_per_million: float = Field(ge=0)
    cached_read_price_per_million: float | None = Field(default=None, ge=0)
    cached_write_price_per_million: float | None = Field(default=None, ge=0)
    enabled: bool = True

    @model_validator(mode="after")
    def populate_defaults(self):
        if not self.model:
            self.model = self.id
        if not self.label:
            self.label = self.id
        return self

class Settings(BaseSettings):
    model_config = settings_config()

    database_url: str = Field(default="")
    app_db_host: str = Field(default="")
    app_db_name: str = Field(default="tertius")
    app_db_owner: str = Field(default="tertius")
    app_db_password: str = Field(default="")
    keycloak_issuer: str = Field(default="http://localhost:8080/realms/tertius")
    keycloak_audience: str = Field(default="tertius-api")
    keycloak_authorized_party: str = Field(default="tertius-ui")
    keycloak_jwks_url_override: str | None = Field(default=None)
    oidc_client_id: str = Field(default="tertius-ui")
    oidc_client_secret: str = Field(default="")
    auth_session_secret: str = Field(default="")
    auth_session_cookie_name: str = Field(default="tertius_session")
    auth_csrf_cookie_name: str = Field(default="tertius_csrf")
    auth_oauth_state_cookie_name: str = Field(default="tertius_oauth_state")
    auth_cookie_secure: bool = Field(default=True)
    auth_allow_insecure_oauth_state_secret: bool = Field(default=False)
    auth_session_idle_seconds: int = Field(default=604800, gt=0)
    auth_session_max_seconds: int = Field(default=2592000, gt=0)
    artifact_retention_limit: int = Field(default=10)
    nats_url: str = Field(default="nats://localhost:4222")
    compile_stream_name: str = Field(default="TERTIUS_COMPILE")
    compile_request_subject: str = Field(default="tertius.compile.request")
    compile_result_subject: str = Field(default="tertius.compile.result")
    compile_worker_queue: str = Field(default="compile-workers")
    compile_result_consumer: str = Field(default="compile-result-api")
    compile_ack_wait_seconds: int = Field(default=900)
    compile_max_deliver: int = Field(default=1)
    compile_timeout_seconds: int = Field(default=600)
    compile_request_max_bytes: int = Field(default=8 * 1024 * 1024)
    compile_result_max_bytes: int = Field(default=90 * 1024 * 1024)
    llm_models_json: str = Field(default="[]")
    llm_default_model_id: str = Field(default="")
    llm_weekly_budget_usd: float = Field(default=14.0, ge=0)
    llm_daily_budget_usd: float | None = Field(default=None, ge=0)
    llm_api_key: str = Field(default="")
    llm_file_edit_system_prompt: str = Field(default="")
    llm_timeout_seconds: int = Field(default=480)
    llm_max_output_tokens: int = Field(default=2048)
    llm_file_edit_max_output_tokens: int = Field(default=65536, gt=0)
    llm_file_edit_max_context_files: int = Field(default=20, ge=1, le=20)
    llm_file_edit_max_context_chars: int = Field(default=80000, gt=0)
    llm_file_edit_max_generation_attempts: int = Field(default=2, ge=1, le=10)
    llm_file_edit_max_rate_limit_attempts: int = Field(default=4, ge=1, le=10)
    llm_file_edit_rate_limit_backoff_base_seconds: float = Field(default=2.0, ge=0.0)
    llm_file_edit_rate_limit_backoff_cap_seconds: float = Field(default=30.0, ge=0.0)
    llm_user_rate_limit_per_minute: int = Field(default=10)
    llm_tenant_rate_limit_per_minute: int = Field(default=60)
    llm_tenant_daily_token_quota: int = Field(default=3200000)
    llm_user_daily_token_quota: int = Field(default=3200000)
    billing_stream_name: str = Field(default="TERTIUS_BILLING")
    billing_llm_usage_subject: str = Field(default="tertius.billing.usage.llm.tokens")
    billing_max_bytes: int = Field(default=256 * 1024)
    allowed_origins: str = Field(default="http://localhost:5173")
    billing_rate_cents_per_hour: int = Field(default=100)
    billing_format_multiplier_stl: float = Field(default=1.0)
    billing_format_multiplier_step: float = Field(default=1.5)
    billing_format_multiplier_gltf: float = Field(default=2.0)
    billing_format_multiplier_glb: float = Field(default=2.0)
    otel_enabled: bool = Field(default=True)
    otel_service_name: str = Field(default="tertius-api")
    otel_exporter_otlp_endpoint: str = Field(default="")
    otel_exporter_otlp_protocol: str = Field(default="grpc")
    otel_traces_sampler: str = Field(default="parentbased_traceidratio")
    otel_traces_sampler_arg: str = Field(default="1.0")
    otel_resource_attributes: str = Field(default="")
    otel_log_json: bool = Field(default=True)

    @model_validator(mode="after")
    def populate_weekly_llm_budget(self):
        if self.llm_daily_budget_usd is not None and "llm_weekly_budget_usd" not in self.model_fields_set:
            self.llm_weekly_budget_usd = round(self.llm_daily_budget_usd * 7, 8)
        return self

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

    @property
    def llm_models(self) -> list[LlmModelConfig]:
        raw = self.llm_models_json.strip()
        parsed: object
        if not raw:
            parsed = []
        else:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("LLM_MODELS_JSON must be valid JSON") from exc

        if not isinstance(parsed, list):
            raise ValueError("LLM_MODELS_JSON must be a JSON array")

        try:
            models = [LlmModelConfig.model_validate(item) for item in parsed]
        except ValidationError as exc:
            raise ValueError(f"LLM_MODELS_JSON contains an invalid model entry: {exc}") from exc

        return models

    @property
    def enabled_llm_models(self) -> list[LlmModelConfig]:
        return [model for model in self.llm_models if model.enabled]

    def get_llm_model(self, model_id: str | None = None) -> LlmModelConfig:
        models = self.enabled_llm_models
        if not models:
            raise ValueError("LLM models are not configured")

        if model_id:
            for model in models:
                if model.id == model_id:
                    return model
            raise ValueError("Requested LLM model is not configured")

        if self.llm_default_model_id:
            for model in models:
                if model.id == self.llm_default_model_id:
                    return model
            raise ValueError("Default LLM model is not configured")

        return models[0]


@lru_cache
def get_settings() -> Settings:
    return Settings()
