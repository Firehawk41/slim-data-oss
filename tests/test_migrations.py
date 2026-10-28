"""slim-data's migrations: reversible, matching the models, and kept to slim-data's own tables."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from slim_data.domain.enums import ReadingRole
from slim_data.persistence import (
    migrate,
    tables,  # noqa: F401  (registers the tables on Base)
)
from slim_data.persistence.db import Base
from slim_data.persistence.observations import record_run, register_batch
from tests.factories import ANA
from tests.persistence.conftest import make_batch, make_reading, make_run


def _version(connection) -> str | None:
    return MigrationContext.configure(
        connection, opts={"version_table": migrate.VERSION_TABLE}
    ).get_current_revision()


def test_upgrade_downgrade_upgrade(engine):
    with engine.begin() as connection:
        migrate.upgrade(connection)
        assert _version(connection) == "0009"
        migrate.downgrade(connection)
        assert _version(connection) is None
        migrate.upgrade(connection)
        assert _version(connection) == "0009"
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


REBUILT = ("runs", "readings", "prep_batch_samples", "prep_batch_spikes", "result_events")  # 0003–0005


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


def test_downgrading_past_0005_keeps_stored_qc_readings_valid(engine):
    """Append-only: a CHECK reading can't be removed, so 0005's downgrade keeps the condition."""
    with engine.begin() as connection:
        migrate.upgrade(connection)
        try:
            # In a savepoint, rolled back at the end: the full downgrade below expects no rows.
            session = Session(bind=connection, join_transaction_mode="create_savepoint")
            batch = register_batch(session, make_batch(), ANA)
            record_run(session, make_run(), [(batch.code, make_reading(None, ReadingRole.CHECK, 1, "9.9"))])
            session.flush()
            migrate.downgrade(connection, "0004")
            migrate.upgrade(connection)
            assert _version(connection) == "0009"
            session.rollback()
            session.close()
        finally:
            migrate.downgrade(connection)
