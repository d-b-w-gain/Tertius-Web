import core.config as config
from core.config import Settings




def test_settings_parse_allowed_origins():
    settings = Settings(
        database_url="postgresql+psycopg://tertius:tertius@localhost:5432/tertius",
        keycloak_issuer="http://localhost:8080/realms/tertius",
        keycloak_audience="tertius-web",
        artifact_root="/tmp/tertius-artifacts",
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
        keycloak_audience="tertius-web",
        artifact_root="/tmp/tertius-artifacts",
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
                "ARTIFACT_ROOT=/tmp/env-artifacts",
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
        "ARTIFACT_ROOT",
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
    assert settings.artifact_root == "/tmp/env-artifacts"
    assert settings.allowed_origin_list == ["https://env.example.test"]
