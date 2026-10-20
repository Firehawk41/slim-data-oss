"""Run slim-data's migrations from Python, e.g. from the host app at startup or from tests.

slim-data shares one database with other modules, so it keeps its own Alembic version table
(`VERSION_TABLE`) and only ever migrates the tables declared on `db.Base`.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection

VERSION_TABLE = "alembic_version_slim_data"
_HERE = Path(__file__).resolve().parent


def alembic_config(connection: Connection | None = None) -> Config:
    config = Config(str(_HERE / "alembic.ini"))
    config.set_main_option("script_location", str(_HERE / "migrations"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def upgrade(connection: Connection, revision: str = "head") -> None:
    command.upgrade(alembic_config(connection), revision)


def downgrade(connection: Connection, revision: str = "base") -> None:
    command.downgrade(alembic_config(connection), revision)
