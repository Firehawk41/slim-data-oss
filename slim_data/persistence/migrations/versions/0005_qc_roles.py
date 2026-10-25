"""QC roles (D-87): check standards and CRMs. Their expected values per batch (`prep_batch_qc`), and
readings of roles 4 (CHECK) and 5 (CRM) have no sample, like a blank.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from slim_data.persistence import append_only, types

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONDITION = (
    "(role IN (3, 4, 5) AND tr_number IS NULL AND position IS NULL)"
    " OR (role NOT IN (3, 4, 5) AND tr_number IS NOT NULL AND position IS NOT NULL)"
)


def _swap_check(drop: str, create: str) -> None:
    with op.batch_alter_table("readings") as batch:
        batch.drop_constraint(drop, type_="check")
        batch.create_check_constraint(create, CONDITION)
    if op.get_bind().dialect.name == "sqlite":
        append_only.install(op.get_bind(), ("readings",))  # rebuilding the table dropped them


def upgrade() -> None:
    op.create_table(
        "prep_batch_qc",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("analyte_code", sa.String(length=20), nullable=False),
        sa.Column("expected", types.ExactDecimal(), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("material", sa.String(length=100), nullable=True),
        sa.Column("lot", sa.String(length=50), nullable=True),
        sa.Column("created_at", types.UtcDateTime(), server_default=types.db_now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["prep_batches.id"], name=op.f("fk_prep_batch_qc_batch_id_prep_batches")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prep_batch_qc")),
        sa.UniqueConstraint(
            "batch_id", "role", "analysis_id", "analyte_code", name="uq_prep_batch_qc_expected"
        ),
    )
    append_only.install(op.get_bind(), ("prep_batch_qc",))
    _swap_check(op.f("ck_readings_blank_has_no_sample"), op.f("ck_readings_batch_role_has_no_sample"))


def downgrade() -> None:
    # The old name, but the condition stays: for roles 1–3 it says exactly what 0002's did, and CHECK
    # and CRM readings already stored stay valid (append-only: they can't be removed, D-04).
    _swap_check(op.f("ck_readings_batch_role_has_no_sample"), op.f("ck_readings_blank_has_no_sample"))
    op.drop_table("prep_batch_qc")
