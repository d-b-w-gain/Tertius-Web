import os

from tests.conftest import docker_client_timeout, postgres_test_image


def test_postgres_test_image_defaults_to_docker_hub(monkeypatch):
    monkeypatch.delenv("POSTGRES_TEST_IMAGE", raising=False)

    assert postgres_test_image() == "postgres:18"


def test_postgres_test_image_can_be_overridden(monkeypatch):
    monkeypatch.setenv("POSTGRES_TEST_IMAGE", "public.ecr.aws/docker/library/postgres:18")

    assert postgres_test_image() == "public.ecr.aws/docker/library/postgres:18"


def test_docker_client_timeout_defaults_to_ci_sized_value(monkeypatch):
    monkeypatch.delenv("DOCKER_CLIENT_TIMEOUT", raising=False)

    assert docker_client_timeout() == 300


def test_docker_client_timeout_can_be_overridden(monkeypatch):
    monkeypatch.setenv("DOCKER_CLIENT_TIMEOUT", "120")

    assert docker_client_timeout() == 120
