"""Solo sign-offs (D-68): a review or release a chemist working alone did on their own results.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from slim_data.persistence import append_only

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("result_events", sa.Column("solo", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("result_events") as batch:
        batch.drop_column("solo")
    if op.get_bind().dialect.name == "sqlite":
        append_only.install(op.get_bind(), ("result_events",))  # rebuilding the table dropped them
