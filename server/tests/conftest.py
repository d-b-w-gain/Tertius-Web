from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.postgres import PostgresContainer

from core.db import Base
import core.models


@pytest.fixture(scope="session")
def postgres_url() -> Generator[str, None, None]:
    with PostgresContainer("postgres:16") as postgres:
        host = postgres.get_container_host_ip()
        port = postgres.get_exposed_port(5432)
        username = postgres.username
        password = postgres.password
        database = postgres.dbname
        yield f"postgresql+psycopg://{username}:{password}@{host}:{port}/{database}"


@pytest.fixture()
def db_session(postgres_url: str) -> Generator[Session, None, None]:
    engine = create_engine(postgres_url, pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with TestingSessionLocal() as session:
        yield session
    Base.metadata.drop_all(engine)
    engine.dispose()
