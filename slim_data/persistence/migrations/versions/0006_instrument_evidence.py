"""Instrument data in (D-88, D-89): each run's source kind, the file itself (`run_files`), its
calibration per analyte (`run_calibrations`), and the instrument's evidence on each reading
(replicate RSD, internal standard recovery), when it has any.

Runs imported before this one came from generic_csv/1, so their source kind is CSV; their files
weren't kept, so they have no `run_files` row.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from slim_data.persistence import append_only, types

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("source_kind", sa.Integer(), server_default="2", nullable=False))
    op.add_column("readings", sa.Column("rsd", types.ExactDecimal(), nullable=True))
    op.add_column("readings", sa.Column("istd_recovery", types.ExactDecimal(), nullable=True))
    op.create_table(
        "run_files",
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", types.UtcDateTime(), server_default=types.db_now(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name=op.f("fk_run_files_run_id_runs")),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_run_files")),
    )
    op.create_table(
        "run_calibrations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("analyte_code", sa.String(length=20), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("correlation", types.ExactDecimal(), nullable=True),
        sa.Column("determination", types.ExactDecimal(), nullable=True),
        sa.Column("lod", types.ExactDecimal(), nullable=True),
        sa.Column("bec", types.ExactDecimal(), nullable=True),
        sa.Column("created_at", types.UtcDateTime(), server_default=types.db_now(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name=op.f("fk_run_calibrations_run_id_runs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_calibrations")),
        sa.UniqueConstraint("run_id", "analysis_id", "analyte_code", name="uq_run_calibrations_analyte"),
    )
    append_only.install(op.get_bind(), ("run_files", "run_calibrations"))


def downgrade() -> None:
    op.drop_table("run_calibrations")
    op.drop_table("run_files")
    # Plain DROP COLUMN (SQLite 3.35+), not a batch rebuild: rebuilding `runs` would drop a table that
    # stored readings point at, and its append-only triggers with it.
    for table, column in (("readings", "istd_recovery"), ("readings", "rsd"), ("runs", "source_kind")):
        op.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
