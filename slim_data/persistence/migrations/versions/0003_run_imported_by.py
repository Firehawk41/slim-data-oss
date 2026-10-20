"""Who uploaded each run, beside the analyst the file names (D-66).

Nullable: a run stored before this revision has no separate uploader (there are none outside tests).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from slim_data.persistence import append_only

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("imported_by_id", sa.String(length=100), nullable=True))
    op.add_column("runs", sa.Column("imported_by", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("imported_by")
        batch.drop_column("imported_by_id")
    if op.get_bind().dialect.name == "sqlite":
        # SQLite drops a column by rebuilding the table, which loses its triggers: put them back.
        append_only.install(op.get_bind(), ("runs",))
