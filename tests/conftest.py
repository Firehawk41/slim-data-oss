"""Shared fixtures. Tests run on a fresh SQLite file by default, or on Postgres when
TEST_DATABASE_URL is set (as in CI): e.g. postgresql+psycopg2://postgres:postgres@localhost/slim_data_test
"""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from slim_data.persistence import migrate
from slim_data.persistence.db import make_engine


@pytest.fixture
def database_url(tmp_path) -> str:
    return os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'slim_data_test.db'}"


@pytest.fixture
def engine(database_url) -> Iterator[Engine]:
    engine = make_engine(database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine) -> Iterator[Session]:
    """A freshly migrated schema inside one transaction, rolled back afterwards (Postgres and SQLite
    both roll back DDL), so tests never see each other's rows."""
    with engine.connect() as connection:
        transaction = connection.begin()
        migrate.upgrade(connection)
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()
