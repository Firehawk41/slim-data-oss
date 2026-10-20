"""Imports (D-66): who uploaded each run, beside the analyst the file names; and attempts, so a cup
measured again (rejected in the run and rerun) keeps every reading.

`imported_by` is nullable: a run stored before this revision has no separate uploader (there are none
outside tests). Existing readings are attempt 1. A batch's sample and spike rows get `created_at`,
since a later file may add to a batch and `as_of` must see when (existing rows: the upgrade time).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from slim_data.persistence import append_only, types

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("imported_by_id", sa.String(length=100), nullable=True))
    op.add_column("runs", sa.Column("imported_by", sa.JSON(), nullable=True))
    op.add_column("readings", sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))
    for table in ("prep_batch_samples", "prep_batch_spikes"):
        op.add_column(
            table, sa.Column("created_at", types.UtcDateTime(), nullable=False, server_default=types.db_now())
        )


def downgrade() -> None:
    for table in ("prep_batch_samples", "prep_batch_spikes"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("created_at")
    with op.batch_alter_table("readings") as batch:
        batch.drop_column("attempt")
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("imported_by")
        batch.drop_column("imported_by_id")
    if op.get_bind().dialect.name == "sqlite":
        # SQLite drops a column by rebuilding the table, which loses its triggers: put them back.
        append_only.install(op.get_bind(), ("runs", "readings", "prep_batch_samples", "prep_batch_spikes"))
