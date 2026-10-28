"""Run order (D-97): each reading's line in its run's file, so check standards can bracket samples.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from slim_data.persistence import append_only

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("readings", sa.Column("sequence", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("readings") as batch:
        batch.drop_column("sequence")
    if op.get_bind().dialect.name == "sqlite":
        append_only.install(op.get_bind(), ("readings",))  # rebuilding the table dropped them
