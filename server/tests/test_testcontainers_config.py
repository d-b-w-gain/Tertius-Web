from tests.conftest import postgres_test_image


def test_postgres_test_image_defaults_to_docker_hub(monkeypatch):
    monkeypatch.delenv("POSTGRES_TEST_IMAGE", raising=False)

    assert postgres_test_image() == "postgres:18"


def test_postgres_test_image_can_be_overridden(monkeypatch):
    monkeypatch.setenv("POSTGRES_TEST_IMAGE", "public.ecr.aws/docker/library/postgres:18")

    assert postgres_test_image() == "public.ecr.aws/docker/library/postgres:18"
