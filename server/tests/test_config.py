import core.config as config
from core.config import Settings


def test_settings_parse_allowed_origins():
    settings = Settings(
        database_url="postgresql+psycopg://tertius:tertius@localhost:5432/tertius",
        keycloak_issuer="http://localhost:8080/realms/tertius",
        keycloak_audience="tertius-api",
        allowed_origins="http://localhost:5173,https://app.example.com",
    )

    assert settings.allowed_origin_list == ["http://localhost:5173", "https://app.example.com"]


def test_settings_builds_database_url_from_chart_database_env():
    settings = Settings(
        database_url="",
        app_db_host="tertius-postgres-rw",
        app_db_name="tertius",
        app_db_owner="tertius",
        app_db_password="secret with spaces",
        keycloak_issuer="http://localhost:8080/realms/tertius",
        keycloak_audience="tertius-api",
    )

    assert settings.database_url == "postgresql+psycopg://tertius:secret+with+spaces@tertius-postgres-rw:5432/tertius"


def test_settings_loads_server_env_when_cwd_is_elsewhere(monkeypatch, tmp_path):
    env_dir = tmp_path / "server"
    env_dir.mkdir()
    env_file = env_dir / ".env"

    env_file.write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql+psycopg://env:env@localhost:5432/envdb",
                "KEYCLOAK_ISSUER=http://keycloak.example.test/realms/env",
                "KEYCLOAK_AUDIENCE=env-audience",
                "KEYCLOAK_AUTHORIZED_PARTY=env-ui",
                "ALLOWED_ORIGINS=https://env.example.test",
            ]
        ),
        encoding="utf-8",
    )

    for env_var in (
        "DATABASE_URL",
        "KEYCLOAK_ISSUER",
        "KEYCLOAK_AUDIENCE",
        "KEYCLOAK_AUTHORIZED_PARTY",
        "ALLOWED_ORIGINS",
    ):
        monkeypatch.delenv(env_var, raising=False)

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(config, "SERVER_ENV_FILE", env_file)

    class PatchedSettings(Settings):
        model_config = config.settings_config()

    settings = PatchedSettings()

    assert settings.database_url == "postgresql+psycopg://env:env@localhost:5432/envdb"
    assert settings.keycloak_issuer == "http://keycloak.example.test/realms/env"
    assert settings.keycloak_audience == "env-audience"
    assert settings.keycloak_authorized_party == "env-ui"
    assert settings.allowed_origin_list == ["https://env.example.test"]



def test_settings_exposes_compile_nats_defaults(monkeypatch):
    for env_var in (
        "NATS_URL",
        "COMPILE_STREAM_NAME",
        "COMPILE_REQUEST_SUBJECT",
        "COMPILE_RESULT_SUBJECT",
        "COMPILE_WORKER_QUEUE",
        "COMPILE_RESULT_CONSUMER",
        "COMPILE_ACK_WAIT_SECONDS",
        "COMPILE_MAX_DELIVER",
        "COMPILE_TIMEOUT_SECONDS",
        "COMPILE_REQUEST_MAX_BYTES",
        "COMPILE_RESULT_MAX_BYTES",
    ):
        monkeypatch.delenv(env_var, raising=False)

    settings = Settings()

    assert settings.nats_url == "nats://localhost:4222"
    assert settings.compile_stream_name == "TERTIUS_COMPILE"
    assert settings.compile_request_subject == "tertius.compile.request"
    assert settings.compile_result_subject == "tertius.compile.result"
    assert settings.compile_worker_queue == "compile-workers"
    assert settings.compile_result_consumer == "compile-result-api"
    assert settings.compile_ack_wait_seconds == 900
    assert settings.compile_ack_wait_seconds > settings.compile_timeout_seconds
    assert settings.compile_max_deliver == 1
    assert settings.compile_timeout_seconds == 600
    assert settings.compile_request_max_bytes == 8 * 1024 * 1024
    assert settings.compile_result_max_bytes == 90 * 1024 * 1024


def test_settings_allows_compile_nats_overrides(monkeypatch):
    monkeypatch.setenv("NATS_URL", "nats://nats.tertius.svc:4222")
    monkeypatch.setenv("COMPILE_STREAM_NAME", "CUSTOM_COMPILE")
    monkeypatch.setenv("COMPILE_REQUEST_SUBJECT", "custom.compile.request")
    monkeypatch.setenv("COMPILE_RESULT_SUBJECT", "custom.compile.result")
    monkeypatch.setenv("COMPILE_WORKER_QUEUE", "custom-workers")
    monkeypatch.setenv("COMPILE_RESULT_CONSUMER", "custom-result-api")
    monkeypatch.setenv("COMPILE_ACK_WAIT_SECONDS", "900")
    monkeypatch.setenv("COMPILE_MAX_DELIVER", "5")
    monkeypatch.setenv("COMPILE_TIMEOUT_SECONDS", "840")
    monkeypatch.setenv("COMPILE_REQUEST_MAX_BYTES", "1048576")
    monkeypatch.setenv("COMPILE_RESULT_MAX_BYTES", "2097152")

    settings = Settings()

    assert settings.nats_url == "nats://nats.tertius.svc:4222"
    assert settings.compile_stream_name == "CUSTOM_COMPILE"
    assert settings.compile_request_subject == "custom.compile.request"
    assert settings.compile_result_subject == "custom.compile.result"
    assert settings.compile_worker_queue == "custom-workers"
    assert settings.compile_result_consumer == "custom-result-api"
    assert settings.compile_ack_wait_seconds == 900
    assert settings.compile_max_deliver == 5
    assert settings.compile_timeout_seconds == 840
    assert settings.compile_request_max_bytes == 1048576
    assert settings.compile_result_max_bytes == 2097152


def test_settings_exposes_llm_and_billing_defaults(monkeypatch):
    for env_var in (
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_API_KEY",
        "LLM_FILE_EDIT_SYSTEM_PROMPT",
        "LLM_TIMEOUT_SECONDS",
        "LLM_MAX_OUTPUT_TOKENS",
        "LLM_FILE_EDIT_MAX_OUTPUT_TOKENS",
        "LLM_FILE_EDIT_MAX_CONTEXT_FILES",
        "LLM_FILE_EDIT_MAX_CONTEXT_CHARS",
        "LLM_USER_RATE_LIMIT_PER_MINUTE",
        "LLM_TENANT_RATE_LIMIT_PER_MINUTE",
        "LLM_TENANT_DAILY_TOKEN_QUOTA",
        "LLM_USER_DAILY_TOKEN_QUOTA",
        "BILLING_STREAM_NAME",
        "BILLING_LLM_USAGE_SUBJECT",
        "BILLING_MAX_BYTES",
    ):
        monkeypatch.delenv(env_var, raising=False)

    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("LLM_API_KEY", "")

    settings = Settings()

    assert settings.llm_base_url == ""
    assert settings.llm_model == ""
    assert settings.llm_api_key == ""
    assert settings.llm_file_edit_system_prompt.startswith(
        "You are the Tertius Intus CAD editing agent."
    )
    assert settings.llm_timeout_seconds == 60
    assert settings.llm_max_output_tokens == 2048
    assert settings.llm_file_edit_max_output_tokens == 65536
    assert settings.llm_file_edit_max_context_files == 20
    assert settings.llm_file_edit_max_context_chars == 80000
    assert settings.llm_user_rate_limit_per_minute == 10
    assert settings.llm_tenant_rate_limit_per_minute == 60
    assert settings.llm_tenant_daily_token_quota == 3200000
    assert settings.llm_user_daily_token_quota == 3200000
    assert settings.billing_stream_name == "TERTIUS_BILLING"
    assert settings.billing_llm_usage_subject == "tertius.billing.usage.llm.tokens"
    assert settings.billing_max_bytes == 262144


def test_settings_allows_llm_and_billing_overrides(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("LLM_MODEL", "test-openai-compatible-model")
    monkeypatch.setenv("LLM_API_KEY", "secret-key")
    monkeypatch.setenv("LLM_FILE_EDIT_SYSTEM_PROMPT", "custom file edit prompt")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "1024")
    monkeypatch.setenv("LLM_FILE_EDIT_MAX_OUTPUT_TOKENS", "4096")
    monkeypatch.setenv("LLM_FILE_EDIT_MAX_CONTEXT_FILES", "6")
    monkeypatch.setenv("LLM_FILE_EDIT_MAX_CONTEXT_CHARS", "50000")
    monkeypatch.setenv("LLM_USER_RATE_LIMIT_PER_MINUTE", "5")
    monkeypatch.setenv("LLM_TENANT_RATE_LIMIT_PER_MINUTE", "25")
    monkeypatch.setenv("LLM_TENANT_DAILY_TOKEN_QUOTA", "50000")
    monkeypatch.setenv("LLM_USER_DAILY_TOKEN_QUOTA", "10000")
    monkeypatch.setenv("BILLING_STREAM_NAME", "CUSTOM_BILLING")
    monkeypatch.setenv("BILLING_LLM_USAGE_SUBJECT", "custom.billing.llm")
    monkeypatch.setenv("BILLING_MAX_BYTES", "65536")

    settings = Settings()

    assert settings.llm_base_url == "https://llm.example.test/v1"
    assert settings.llm_model == "test-openai-compatible-model"
    assert settings.llm_api_key == "secret-key"
    assert settings.llm_file_edit_system_prompt == "custom file edit prompt"
    assert settings.llm_timeout_seconds == 30
    assert settings.llm_max_output_tokens == 1024
    assert settings.llm_file_edit_max_output_tokens == 4096
    assert settings.llm_file_edit_max_context_files == 6
    assert settings.llm_file_edit_max_context_chars == 50000
    assert settings.llm_user_rate_limit_per_minute == 5
    assert settings.llm_tenant_rate_limit_per_minute == 25
    assert settings.llm_tenant_daily_token_quota == 50000
    assert settings.llm_user_daily_token_quota == 10000
    assert settings.billing_stream_name == "CUSTOM_BILLING"
    assert settings.billing_llm_usage_subject == "custom.billing.llm"
    assert settings.billing_max_bytes == 65536
