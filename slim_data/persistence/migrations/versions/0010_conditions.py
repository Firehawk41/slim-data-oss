"""Instrument conditions (D-106): the conditions a reading and a calibration were measured under,
e.g. the plasma mode, so the quality charts keep each mode a series of its own.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from slim_data.persistence import append_only

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("readings", "run_calibrations")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("conditions", sa.String(50), nullable=True))


def downgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column("conditions")
    if op.get_bind().dialect.name == "sqlite":
        append_only.install(op.get_bind(), TABLES)  # rebuilding the tables dropped them
