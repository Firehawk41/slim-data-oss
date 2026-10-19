"""slim-data attaches to the OSS demo ecosystem only (slim_data/persistence/oss_guard.py)."""

import re
from pathlib import Path

import pytest

import slim_data
from slim_data.persistence.db import make_engine
from slim_data.persistence.oss_guard import ALLOWED_HOSTS_ENV, NotAnOssDatabase, assert_oss_database

PACKAGE = Path(slim_data.__file__).resolve().parent


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///demo.db",
        "sqlite://",
        "postgresql+psycopg2://postgres:postgres@localhost:5432/slim",
        "postgresql+psycopg2://postgres@127.0.0.1:5433/slim_demo",
        "postgresql+psycopg2://postgres:postgres@db:5432/slim",  # docker-compose service
    ],
)
def test_demo_databases_are_allowed(url):
    assert_oss_database(url)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg2://u:p@precilab-prod.example.com/slim",
        "postgresql+psycopg2://u:p@localhost/PreciLab_production",
        "sqlite:///C:/PreciLab/slim.db",
        "postgresql+psycopg2://precilab_app:p@localhost/slim",
    ],
)
def test_precilab_is_always_refused(url, monkeypatch):
    monkeypatch.setenv(ALLOWED_HOSTS_ENV, "precilab-prod.example.com")  # no override exists
    with pytest.raises(NotAnOssDatabase, match="precilab"):
        assert_oss_database(url)


def test_unknown_hosts_are_refused_unless_listed(monkeypatch):
    url = "postgresql+psycopg2://u:p@lab-server.internal:5432/slim"
    with pytest.raises(NotAnOssDatabase, match="lab-server.internal"):
        assert_oss_database(url)
    monkeypatch.setenv(ALLOWED_HOSTS_ENV, "demo-db.example.dev, lab-server.internal")
    assert_oss_database(url)


def test_make_engine_enforces_the_guard():
    with pytest.raises(NotAnOssDatabase):
        make_engine("postgresql+psycopg2://u:p@lab-server.internal/slim")


def create_engine_callers(package: Path) -> list[str]:
    """Files other than persistence/db.py that call create_engine (and so could bypass the guard)."""
    return sorted(
        str(path.relative_to(package))
        for path in package.rglob("*.py")
        if path != package / "persistence" / "db.py" and re.search(r"\bcreate_engine\s*\(", path.read_text())
    )


def test_nothing_bypasses_make_engine():
    assert create_engine_callers(PACKAGE) == []


def test_the_bypass_check_catches_a_create_engine_call(tmp_path):
    (tmp_path / "persistence").mkdir()
    (tmp_path / "persistence" / "db.py").write_text("engine = create_engine(url)\n")
    (tmp_path / "sneaky.py").write_text("from sqlalchemy import create_engine\nengine = create_engine(url)\n")
    assert create_engine_callers(tmp_path) == ["sneaky.py"]
