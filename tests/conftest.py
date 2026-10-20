"""Shared fixtures. Tests run on a fresh SQLite file by default, or on Postgres when
TEST_DATABASE_URL is set (as in CI): e.g. postgresql+psycopg2://postgres:postgres@localhost/slim_data_test
"""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine

from slim_data.persistence.db import make_engine


@pytest.fixture
def database_url(tmp_path) -> str:
    return os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'slim_data_test.db'}"


@pytest.fixture
def engine(database_url) -> Iterator[Engine]:
    engine = make_engine(database_url)
    yield engine
    engine.dispose()
