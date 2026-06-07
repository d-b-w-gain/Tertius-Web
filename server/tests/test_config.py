from core.config import Settings


def test_settings_build_keycloak_jwks_url():
    settings = Settings(
        database_url="postgresql+psycopg://tertius:tertius@localhost:5432/tertius",
        keycloak_issuer="http://localhost:8080/realms/tertius",
        keycloak_audience="tertius-web",
        artifact_root="/tmp/tertius-artifacts",
    )

    assert settings.keycloak_jwks_url == "http://localhost:8080/realms/tertius/protocol/openid-connect/certs"


def test_settings_parse_allowed_origins():
    settings = Settings(
        database_url="postgresql+psycopg://tertius:tertius@localhost:5432/tertius",
        keycloak_issuer="http://localhost:8080/realms/tertius",
        keycloak_audience="tertius-web",
        artifact_root="/tmp/tertius-artifacts",
        allowed_origins="http://localhost:5173,https://app.example.com",
    )

    assert settings.allowed_origin_list == ["http://localhost:5173", "https://app.example.com"]
