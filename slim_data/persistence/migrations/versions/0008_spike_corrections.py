"""Spike amounts are versioned (D-96): a prep correction after handoff is the next version, with why
and who, as the cup dilutions are (0007).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from slim_data.persistence import append_only

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEY = ["batch_id", "tr_number", "position", "analysis_id", "analyte_code"]


def _reinstall_triggers() -> None:
    if op.get_bind().dialect.name == "sqlite":
        append_only.install(op.get_bind(), ("prep_batch_spikes",))  # rebuilding the table dropped them


def upgrade() -> None:
    with op.batch_alter_table("prep_batch_spikes") as batch:
        batch.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("created_by_id", sa.String(length=100), nullable=True))
        batch.drop_constraint("uq_prep_batch_spikes_spike", type_="unique")
        batch.create_unique_constraint("uq_prep_batch_spikes_spike", [*KEY, "version"])
    _reinstall_triggers()


def downgrade() -> None:
    corrected = op.get_bind().execute(sa.text("SELECT count(*) FROM prep_batch_spikes WHERE version > 1"))
    if corrected.scalar():
        raise RuntimeError("corrected spike amounts are stored: they can't be removed (append-only, D-04)")
    with op.batch_alter_table("prep_batch_spikes") as batch:
        batch.drop_constraint("uq_prep_batch_spikes_spike", type_="unique")
        batch.create_unique_constraint("uq_prep_batch_spikes_spike", KEY)
        batch.drop_column("created_by_id")
        batch.drop_column("reason")
        batch.drop_column("version")
    _reinstall_triggers()
