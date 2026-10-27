"""Cup dilutions recorded by the prep book (D-95): they beat the instrument file's.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from slim_data.persistence import append_only, types

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prep_batch_dilutions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("tr_number", sa.String(length=20), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("role", sa.Integer(), nullable=False),
        sa.Column("replicate", sa.Integer(), nullable=False),
        sa.Column("dilution", types.ExactDecimal(), nullable=False),
        sa.Column("created_at", types.UtcDateTime(), server_default=types.db_now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["prep_batches.id"], name=op.f("fk_prep_batch_dilutions_batch_id_prep_batches")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prep_batch_dilutions")),
        sa.UniqueConstraint(
            "batch_id", "tr_number", "position", "role", "replicate", name="uq_prep_batch_dilutions_cup"
        ),
    )
    append_only.install(op.get_bind(), ("prep_batch_dilutions",))


def downgrade() -> None:
    op.drop_table("prep_batch_dilutions")
