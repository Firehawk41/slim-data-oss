"""slim-data's migrations: reversible, matching the models, and kept to slim-data's own tables."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from slim_data.persistence import (
    migrate,
    tables,  # noqa: F401  (registers the tables on Base)
)
from slim_data.persistence.db import Base


def _version(connection) -> str | None:
    return MigrationContext.configure(
        connection, opts={"version_table": migrate.VERSION_TABLE}
    ).get_current_revision()


def test_upgrade_downgrade_upgrade(engine):
    with engine.begin() as connection:
        migrate.upgrade(connection)
        assert _version(connection) == "0003"
        migrate.downgrade(connection)
        assert _version(connection) is None
        migrate.upgrade(connection)
        assert _version(connection) == "0003"
        migrate.downgrade(connection)


def test_uses_its_own_version_table_and_leaves_other_modules_alone(engine):
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('someone_elses_rev')"))
        connection.execute(text("CREATE TABLE tr_submissions (id INTEGER PRIMARY KEY)"))
        try:
            migrate.upgrade(connection)
            tables = set(inspect(connection).get_table_names())
            assert migrate.VERSION_TABLE in tables
            assert {"alembic_version", "tr_submissions"} <= tables
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
                "someone_elses_rev"
            )
        finally:
            migrate.downgrade(connection)
            connection.execute(text("DROP TABLE alembic_version"))
            connection.execute(text("DROP TABLE tr_submissions"))
            connection.execute(text(f"DROP TABLE {migrate.VERSION_TABLE}"))


def test_models_match_migrations(engine):
    with engine.begin() as connection:
        migrate.upgrade(connection)
        try:
            context = MigrationContext.configure(
                connection,
                opts={
                    "version_table": migrate.VERSION_TABLE,
                    "include_object": lambda obj, name, type_, *_: (
                        type_ != "table" or name in Base.metadata.tables
                    ),
                },
            )
            assert compare_metadata(context, Base.metadata) == []
        finally:
            migrate.downgrade(connection)


REBUILT = ("runs", "readings", "prep_batch_samples", "prep_batch_spikes")  # tables 0003 alters


def test_append_only_survives_a_downgrade(engine):
    with engine.begin() as connection:
        migrate.upgrade(connection)
        migrate.downgrade(connection, "0002")
        try:
            names = ", ".join(f"'{t}'" for t in REBUILT)
            if connection.dialect.name == "sqlite":
                query = f"SELECT count(*) FROM sqlite_master WHERE type = 'trigger' AND tbl_name IN ({names})"
            else:
                query = (
                    "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
                    f" WHERE c.relname IN ({names}) AND NOT t.tgisinternal"
                )
            assert connection.execute(text(query)).scalar() == 2 * len(REBUILT)
        finally:
            migrate.downgrade(connection)
