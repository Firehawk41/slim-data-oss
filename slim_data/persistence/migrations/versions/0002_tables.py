"""slim-data's tables (DESIGN.md §10.1) and the append-only triggers on every one of them.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from slim_data.persistence import append_only, types

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen here, not read from the models: this migration must keep meaning what it meant.
TABLES = (
    "policies",
    "prep_batches",
    "report_issues",
    "results",
    "rulesets",
    "runs",
    "prep_batch_samples",
    "prep_batch_spikes",
    "readings",
    "result_versions",
    "reading_exclusions",
    "report_issue_lines",
    "result_events",
)


def upgrade() -> None:
    op.create_table(
        "policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("published_by_id", sa.String(length=100), nullable=False),
        sa.Column("published_by", sa.JSON(), nullable=False),
        sa.Column("created_at", types.UtcDateTime(), server_default=types.db_now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policies")),
        sa.UniqueConstraint("content_hash", name=op.f("uq_policies_content_hash")),
    )
    op.create_table(
        "prep_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("prep_method", sa.String(length=100), nullable=False),
        sa.Column("created_by_id", sa.String(length=100), nullable=False),
        sa.Column("created_by", sa.JSON(), nullable=False),
        sa.Column("created_at", types.UtcDateTime(), server_default=types.db_now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prep_batches")),
        sa.UniqueConstraint("code", name=op.f("uq_prep_batches_code")),
    )
    op.create_table(
        "report_issues",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tr_number", sa.String(length=20), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("amends_id", sa.Integer(), nullable=True),
        sa.Column("created_by_id", sa.String(length=100), nullable=False),
        sa.Column("created_by", sa.JSON(), nullable=False),
        sa.Column("created_at", types.UtcDateTime(), server_default=types.db_now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["amends_id"], ["report_issues.id"], name=op.f("fk_report_issues_amends_id_report_issues")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_issues")),
    )
    with op.batch_alter_table("report_issues", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_report_issues_tr_number"), ["tr_number"], unique=False)

    op.create_table(
        "results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tr_number", sa.String(length=20), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("analyte_code", sa.String(length=20), nullable=False),
        sa.Column("created_at", types.UtcDateTime(), server_default=types.db_now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_results")),
        sa.UniqueConstraint(
            "tr_number", "position", "analysis_id", "analyte_code", name="uq_results_identity"
        ),
    )
    op.create_table(
        "rulesets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("prep_method", sa.String(length=100), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("cliff_reason", sa.Text(), nullable=True),
        sa.Column("published_by_id", sa.String(length=100), nullable=False),
        sa.Column("published_by", sa.JSON(), nullable=False),
        sa.Column("created_at", types.UtcDateTime(), server_default=types.db_now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rulesets")),
        sa.UniqueConstraint("content_hash", name=op.f("uq_rulesets_content_hash")),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_code", sa.String(length=50), nullable=False),
        sa.Column("instrument", sa.String(length=100), nullable=False),
        sa.Column("analyzed_at", types.UtcDateTime(), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("reader", sa.String(length=50), nullable=False),
        sa.Column("analyst_id", sa.String(length=100), nullable=False),
        sa.Column("analyst", sa.JSON(), nullable=False),
        sa.Column("created_at", types.UtcDateTime(), server_default=types.db_now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
        sa.UniqueConstraint("source_sha256", name=op.f("uq_runs_source_sha256")),
    )
    op.create_table(
        "prep_batch_samples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("tr_number", sa.String(length=20), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["prep_batches.id"], name=op.f("fk_prep_batch_samples_batch_id_prep_batches")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prep_batch_samples")),
        sa.UniqueConstraint("batch_id", "tr_number", "position", name="uq_prep_batch_samples_sample"),
    )
    op.create_table(
        "prep_batch_spikes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("tr_number", sa.String(length=20), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("analyte_code", sa.String(length=20), nullable=False),
        sa.Column("added", types.ExactDecimal(), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["prep_batches.id"], name=op.f("fk_prep_batch_spikes_batch_id_prep_batches")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prep_batch_spikes")),
        sa.UniqueConstraint(
            "batch_id",
            "tr_number",
            "position",
            "analysis_id",
            "analyte_code",
            name="uq_prep_batch_spikes_spike",
        ),
    )
    op.create_table(
        "readings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("tr_number", sa.String(length=20), nullable=True),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("analyte_code", sa.String(length=20), nullable=False),
        sa.Column("role", sa.Integer(), nullable=False),
        sa.Column("replicate", sa.Integer(), nullable=False),
        sa.Column("value", types.ExactDecimal(), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("mdl", types.ExactDecimal(), nullable=True),
        sa.Column("dilution", types.ExactDecimal(), nullable=False),
        sa.Column("created_at", types.UtcDateTime(), server_default=types.db_now(), nullable=False),
        sa.CheckConstraint(
            "(role = 3 AND tr_number IS NULL AND position IS NULL)"
            " OR (role <> 3 AND tr_number IS NOT NULL AND position IS NOT NULL)",
            name=op.f("ck_readings_blank_has_no_sample"),
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["prep_batches.id"], name=op.f("fk_readings_batch_id_prep_batches")
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name=op.f("fk_readings_run_id_runs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_readings")),
    )
    with op.batch_alter_table("readings", schema=None) as batch_op:
        batch_op.create_index("ix_readings_batch", ["batch_id"], unique=False)

    op.create_table(
        "result_versions",
        sa.Column("result_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("ruleset_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("raw_value", types.ExactDecimal(), nullable=True),
        sa.Column("reported_value", types.ExactDecimal(), nullable=True),
        sa.Column("reported_unit", sa.String(length=20), nullable=True),
        sa.Column("qualifier", sa.Integer(), nullable=False),
        sa.Column("uncertainty", types.ExactDecimal(), nullable=True),
        sa.Column("recovery", types.ExactDecimal(), nullable=True),
        sa.Column("flags", sa.JSON(), nullable=False),
        sa.Column("trace", sa.JSON(), nullable=False),
        sa.Column("limits", sa.JSON(), nullable=False),
        sa.Column("analyst_ids", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.String(length=100), nullable=False),
        sa.Column("created_by", sa.JSON(), nullable=False),
        sa.Column("created_at", types.UtcDateTime(), server_default=types.db_now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["prep_batches.id"], name=op.f("fk_result_versions_batch_id_prep_batches")
        ),
        sa.ForeignKeyConstraint(
            ["result_id"], ["results.id"], name=op.f("fk_result_versions_result_id_results")
        ),
        sa.ForeignKeyConstraint(
            ["ruleset_id"], ["rulesets.id"], name=op.f("fk_result_versions_ruleset_id_rulesets")
        ),
        sa.PrimaryKeyConstraint("result_id", "version", name=op.f("pk_result_versions")),
    )
    op.create_table(
        "reading_exclusions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reading_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("created_by_id", sa.String(length=100), nullable=False),
        sa.Column("created_by", sa.JSON(), nullable=False),
        sa.Column("created_at", types.UtcDateTime(), server_default=types.db_now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["reading_id"], ["readings.id"], name=op.f("fk_reading_exclusions_reading_id_readings")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reading_exclusions")),
        sa.UniqueConstraint("reading_id", name=op.f("uq_reading_exclusions_reading_id")),
    )
    op.create_table(
        "report_issue_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("issue_id", sa.Integer(), nullable=False),
        sa.Column("result_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["issue_id"], ["report_issues.id"], name=op.f("fk_report_issue_lines_issue_id_report_issues")
        ),
        sa.ForeignKeyConstraint(
            ["result_id", "version"],
            ["result_versions.result_id", "result_versions.version"],
            name=op.f("fk_report_issue_lines_result_id_result_versions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_issue_lines")),
        sa.UniqueConstraint("issue_id", "result_id", name="uq_report_issue_lines_line"),
    )
    op.create_table(
        "result_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("result_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Integer(), nullable=False),
        sa.Column("reason_category", sa.String(length=100), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.String(length=100), nullable=False),
        sa.Column("actor", sa.JSON(), nullable=False),
        sa.Column("created_at", types.UtcDateTime(), server_default=types.db_now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["result_id", "version"],
            ["result_versions.result_id", "result_versions.version"],
            name=op.f("fk_result_events_result_id_result_versions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_result_events")),
    )
    with op.batch_alter_table("result_events", schema=None) as batch_op:
        batch_op.create_index("ix_result_events_kind", ["kind"], unique=False)
        batch_op.create_index("ix_result_events_result", ["result_id"], unique=False)

    append_only.install(op.get_bind(), TABLES)


def downgrade() -> None:
    append_only.uninstall(op.get_bind())
    with op.batch_alter_table("result_events", schema=None) as batch_op:
        batch_op.drop_index("ix_result_events_result")
        batch_op.drop_index("ix_result_events_kind")

    op.drop_table("result_events")
    op.drop_table("report_issue_lines")
    op.drop_table("reading_exclusions")
    op.drop_table("result_versions")
    with op.batch_alter_table("readings", schema=None) as batch_op:
        batch_op.drop_index("ix_readings_batch")

    op.drop_table("readings")
    op.drop_table("prep_batch_spikes")
    op.drop_table("prep_batch_samples")
    op.drop_table("runs")
    op.drop_table("rulesets")
    op.drop_table("results")
    with op.batch_alter_table("report_issues", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_report_issues_tr_number"))

    op.drop_table("report_issues")
    op.drop_table("prep_batches")
    op.drop_table("policies")
