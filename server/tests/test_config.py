import core.config as config
from core.config import Settings
from pydantic import ValidationError
import pytest


def test_server_env_example_matches_settings_fields():
    env_example = config.SERVER_ENV_FILE.with_name(".env.example")
    actual = {
        line.split("=", 1)[0]
        for line in env_example.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    expected = {name.upper() for name in Settings.model_fields}

    assert actual == expected


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


def test_settings_exposes_otel_defaults(monkeypatch):
    for env_var in (
        "OTEL_ENABLED",
        "OTEL_SERVICE_NAME",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        "OTEL_TRACES_SAMPLER",
        "OTEL_TRACES_SAMPLER_ARG",
        "OTEL_RESOURCE_ATTRIBUTES",
        "OTEL_LOG_JSON",
    ):
        monkeypatch.delenv(env_var, raising=False)

    settings = Settings()

    assert settings.otel_enabled is True
    assert settings.otel_service_name == "tertius-api"
    assert settings.otel_exporter_otlp_endpoint == ""
    assert settings.otel_exporter_otlp_protocol == "grpc"
    assert settings.otel_traces_sampler == "parentbased_traceidratio"
    assert settings.otel_traces_sampler_arg == "1.0"
    assert settings.otel_resource_attributes == ""
    assert settings.otel_log_json is True


def test_settings_allows_otel_overrides(monkeypatch):
    monkeypatch.setenv("OTEL_ENABLED", "false")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "tertius-compile-job")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "always_on")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", "0.25")
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "deployment.environment=test,k8s.namespace.name=tertius")
    monkeypatch.setenv("OTEL_LOG_JSON", "false")

    settings = Settings()

    assert settings.otel_enabled is False
    assert settings.otel_service_name == "tertius-compile-job"
    assert settings.otel_exporter_otlp_endpoint == "http://collector:4317"
    assert settings.otel_exporter_otlp_protocol == "grpc"
    assert settings.otel_traces_sampler == "always_on"
    assert settings.otel_traces_sampler_arg == "0.25"
    assert settings.otel_resource_attributes == "deployment.environment=test,k8s.namespace.name=tertius"
    assert settings.otel_log_json is False


def test_settings_exposes_pi_agent_and_billing_defaults(monkeypatch):
    for env_var in (
        "PI_AGENT_ENABLED", "PI_AGENT_PROVIDER", "PI_AGENT_MODEL", "PI_AGENT_MODEL_LABEL",
        "PI_AGENT_THINKING", "PI_AGENT_TIMEOUT_SECONDS", "PI_AGENT_MAX_TURNS",
        "PI_AGENT_MAX_TOOL_CALLS", "PI_AGENT_ESTIMATED_OUTPUT_TOKENS",
        "PI_AGENT_STREAM_NAME", "PI_AGENT_REQUEST_SUBJECT", "PI_AGENT_RESULT_SUBJECT",
        "PI_AGENT_WORKER_QUEUE", "PI_AGENT_RESULT_CONSUMER", "PI_AGENT_ACK_WAIT_SECONDS",
        "PI_AGENT_MAX_DELIVER", "PI_AGENT_REQUEST_MAX_BYTES", "PI_AGENT_RESULT_MAX_BYTES",
        "PI_AGENT_STREAM_MAX_AGE_SECONDS", "PI_AGENT_STREAM_MAX_BYTES",
        "LLM_USER_RATE_LIMIT_PER_MINUTE",
        "LLM_TENANT_RATE_LIMIT_PER_MINUTE",
        "LLM_TENANT_DAILY_TOKEN_QUOTA",
        "LLM_USER_DAILY_TOKEN_QUOTA",
        "BILLING_STREAM_NAME",
        "BILLING_LLM_USAGE_SUBJECT",
        "BILLING_MAX_BYTES",
    ):
        monkeypatch.delenv(env_var, raising=False)

    settings = Settings()
    assert settings.pi_agent_enabled is False
    assert settings.pi_agent_provider == "openai-codex"
    assert settings.pi_agent_model == "gpt-5.6-sol"
    assert settings.pi_agent_model_label == "GPT-5.6 Sol"
    assert settings.pi_agent_thinking == "medium"
    assert settings.pi_agent_timeout_seconds == 480
    assert settings.pi_agent_max_turns == 12
    assert settings.pi_agent_max_tool_calls == 48
    assert settings.pi_agent_estimated_output_tokens == 65536
    assert settings.pi_agent_request_max_bytes == 3_000_000
    assert settings.pi_agent_result_max_bytes == 3_000_000
    assert settings.pi_agent_stream_name == "TERTIUS_PI_AGENT"
    assert settings.pi_agent_request_subject == "tertius.pi.request"
    assert settings.pi_agent_result_subject == "tertius.pi.result"
    assert settings.pi_agent_worker_queue == "pi-agent-workers"
    assert settings.pi_agent_result_consumer == "pi-agent-result-api"
    assert settings.pi_agent_ack_wait_seconds == 90
    assert settings.pi_agent_max_deliver == 2
    assert settings.pi_agent_stream_max_age_seconds == 86400
    assert settings.pi_agent_stream_max_bytes == 67108864


@pytest.mark.parametrize("field,value", [("pi_agent_thinking", "extreme"), ("pi_agent_max_turns", 0), ("pi_agent_request_max_bytes", 0)])
def test_settings_rejects_invalid_pi_agent_values(field, value):
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_removed_direct_provider_settings_are_absent():
    removed = {
        "pi_agent_system_prompt",
        "llm_api_key", "llm_models_json", "llm_default_model_id",
        "llm_weekly_" + "budget_usd", "llm_daily_" + "budget_usd", "llm_max_output_tokens",
        "llm_file_edit_max_output_tokens", "llm_file_edit_max_generation_attempts",
        "llm_file_edit_max_rate_limit_attempts",
        "llm_file_edit_rate_limit_backoff_base_seconds",
        "llm_file_edit_rate_limit_backoff_cap_seconds",
    }
    assert removed.isdisjoint(Settings.model_fields)


def test_settings_allows_pi_agent_and_billing_overrides(monkeypatch):
    monkeypatch.setenv("PI_AGENT_ENABLED", "true")
    monkeypatch.setenv("PI_AGENT_MODEL", "gpt-test")
    monkeypatch.setenv("PI_AGENT_THINKING", "xhigh")
    monkeypatch.setenv("PI_AGENT_MAX_TURNS", "8")
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

    assert settings.pi_agent_enabled is True
    assert settings.pi_agent_model == "gpt-test"
    assert settings.pi_agent_thinking == "xhigh"
    assert settings.pi_agent_max_turns == 8
    assert settings.llm_file_edit_max_context_files == 6
    assert settings.llm_file_edit_max_context_chars == 50000
    assert settings.llm_user_rate_limit_per_minute == 5
    assert settings.llm_tenant_rate_limit_per_minute == 25
    assert settings.llm_tenant_daily_token_quota == 50000
    assert settings.llm_user_daily_token_quota == 10000
    assert settings.billing_stream_name == "CUSTOM_BILLING"
    assert settings.billing_llm_usage_subject == "custom.billing.llm"
    assert settings.billing_max_bytes == 65536
