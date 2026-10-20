"""Alembic environment: migrates only the tables declared on slim_data.persistence.db.Base.

slim-data shares one database with other modules (slim-domain, the host, invoicing). Two things keep
its migrations to itself:
  * its own version table, `alembic_version_slim_data`, so it never reads or overwrites another
    module's migration history;
  * `include_object`, so autogenerate never proposes changes to tables slim-data doesn't own.
"""

import os
from logging.config import fileConfig

from alembic import context

from slim_data.persistence import tables  # noqa: F401  (registers the tables on Base)
from slim_data.persistence.db import Base, make_engine
from slim_data.persistence.migrate import VERSION_TABLE

config = context.config
if config.config_file_name is not None and config.attributes.get("connection") is None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table":
        return name in target_metadata.tables
    return True


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        include_object=include_object,
        version_table=VERSION_TABLE,
        render_as_batch=True,  # SQLite needs batch mode for ALTERs
        **kwargs,
    )


def _url() -> str:
    url = config.attributes.get("database_url") or os.environ.get("SLIM_DATA_DATABASE_URL")
    if not url:
        raise RuntimeError("Set SLIM_DATA_DATABASE_URL (or pass a connection) to run slim-data migrations.")
    return url


def run_migrations_offline() -> None:
    _configure(url=_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = make_engine(_url())
    with engine.connect() as connection:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
