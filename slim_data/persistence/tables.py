"""slim-data's own tables (DESIGN.md §10.1). Every one is append-only: rows are inserted, never
updated or deleted (D-04), enforced by the repositories (no update path) and by triggers
(`append_only.py`).

Portable: enums as ints, JSON via `JSON`, decimals as `ExactDecimal`, no ARRAY, and no foreign keys
into other modules' tables (samples are natural keys: TR number + position). `created_at` comes
from the database clock (D-21); `as_of` reads compare it, with the row id as the tiebreaker.

People are stored twice: `*_id` (what four-eyes and queries compare) and the whole `Actor` as JSON,
including its identity source (D-50), as it stood at the time.
"""

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from slim_data.persistence.db import Base
from slim_data.persistence.types import ExactDecimal, UtcDateTime, db_now

metadata = Base.metadata


def _created(prefix: str = "created_by") -> list[Column]:
    return [
        Column(f"{prefix}_id", String(100), nullable=False),
        Column(prefix, JSON, nullable=False),
        Column("created_at", UtcDateTime(), nullable=False, server_default=db_now()),
    ]


def _sample(nullable: bool = False) -> list[Column]:
    return [
        Column("tr_number", String(20), nullable=nullable),
        Column("position", Integer, nullable=nullable),
    ]


def _analyte() -> list[Column]:
    return [
        Column("analysis_id", Integer, nullable=False),
        Column("analyte_code", String(20), nullable=False),
    ]


rulesets = Table(
    "rulesets",
    metadata,
    Column("id", Integer, primary_key=True),  # the ruleset's published number (D-14)
    Column("name", String(100), nullable=False),
    Column("analysis_id", Integer, nullable=False),
    Column("prep_method", String(100)),  # NULL = the analysis default (D-15)
    Column("content_hash", String(64), nullable=False, unique=True),
    Column("body", JSON, nullable=False),
    Column("cliff_reason", Text),  # required when publishing raised warnings (D-30)
    *_created("published_by"),
)

policies = Table(
    "policies",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(60), nullable=False),
    Column("content_hash", String(64), nullable=False, unique=True),
    Column("body", JSON, nullable=False),
    *_created("published_by"),
)

prep_batches = Table(
    "prep_batches",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("code", String(50), nullable=False, unique=True),
    Column("analysis_id", Integer, nullable=False),
    Column("prep_method", String(100), nullable=False),  # snapshot (D-15)
    *_created(),
)

prep_batch_samples = Table(
    "prep_batch_samples",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("batch_id", ForeignKey("prep_batches.id"), nullable=False),
    *_sample(),
    UniqueConstraint("batch_id", "tr_number", "position", name="uq_prep_batch_samples_sample"),
)

prep_batch_spikes = Table(
    "prep_batch_spikes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("batch_id", ForeignKey("prep_batches.id"), nullable=False),
    *_sample(),
    *_analyte(),
    Column("added", ExactDecimal(), nullable=False),
    Column("unit", String(20), nullable=False),
    UniqueConstraint(
        "batch_id", "tr_number", "position", "analysis_id", "analyte_code", name="uq_prep_batch_spikes_spike"
    ),
)

runs = Table(
    "runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_code", String(50), nullable=False),
    Column("instrument", String(100), nullable=False),
    Column("analyzed_at", UtcDateTime(), nullable=False),
    Column("source_name", String(255), nullable=False),
    Column("source_sha256", String(64), nullable=False, unique=True),  # one file, one import (§9)
    Column("reader", String(50), nullable=False),
    Column("analyst_id", String(100), nullable=False),  # who the file names (D-66)
    Column("analyst", JSON, nullable=False),
    Column("imported_by_id", String(100)),  # who uploaded it; NULL only before migration 0003
    Column("imported_by", JSON),
    Column("created_at", UtcDateTime(), nullable=False, server_default=db_now()),
)

readings = Table(
    "readings",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", ForeignKey("runs.id"), nullable=False),
    Column("batch_id", ForeignKey("prep_batches.id"), nullable=False),  # a blank belongs here (D-64)
    *_sample(nullable=True),
    *_analyte(),
    Column("role", Integer, nullable=False),
    Column("replicate", Integer, nullable=False),
    Column("value", ExactDecimal(), nullable=False),
    Column("unit", String(20), nullable=False),
    Column("mdl", ExactDecimal()),
    Column("dilution", ExactDecimal(), nullable=False),
    Column("created_at", UtcDateTime(), nullable=False, server_default=db_now()),
    CheckConstraint(
        "(role = 3 AND tr_number IS NULL AND position IS NULL)"
        " OR (role <> 3 AND tr_number IS NOT NULL AND position IS NOT NULL)",
        name="blank_has_no_sample",
    ),
    Index("ix_readings_batch", "batch_id"),
)

reading_exclusions = Table(
    "reading_exclusions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("reading_id", ForeignKey("readings.id"), nullable=False),
    Column("kind", Integer, nullable=False),  # ExclusionKind: the newest row per reading decides
    Column("reason", String(500), nullable=False),
    *_created(),
    Index("ix_reading_exclusions_reading", "reading_id"),
)

results = Table(
    "results",
    metadata,
    Column("id", Integer, primary_key=True),
    *_sample(),
    *_analyte(),
    Column("created_at", UtcDateTime(), nullable=False, server_default=db_now()),
    UniqueConstraint("tr_number", "position", "analysis_id", "analyte_code", name="uq_results_identity"),
)

result_versions = Table(
    "result_versions",
    metadata,
    Column("result_id", ForeignKey("results.id"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("ruleset_id", ForeignKey("rulesets.id"), nullable=False),
    Column("batch_id", ForeignKey("prep_batches.id"), nullable=False),
    Column("raw_value", ExactDecimal()),
    Column("reported_value", ExactDecimal()),
    Column("reported_unit", String(20)),
    Column("qualifier", Integer, nullable=False),
    Column("uncertainty", ExactDecimal()),
    Column("recovery", ExactDecimal()),
    Column("flags", JSON, nullable=False),
    Column("trace", JSON, nullable=False),
    Column("limits", JSON, nullable=False),
    Column("analyst_ids", JSON, nullable=False),
    Column("reason", Text, nullable=False),
    *_created(),
    # The primary key doubles as the optimistic-concurrency check: two writers can't both add v3.
    PrimaryKeyConstraint("result_id", "version"),
)

result_events = Table(
    "result_events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("result_id", Integer, nullable=False),
    Column("version", Integer, nullable=False),
    Column("kind", Integer, nullable=False),  # EventKind; EXCEPTION rows are the exception log (D-65)
    Column("reason_category", String(100), nullable=False),
    Column("note", Text, nullable=False),
    *_created("actor"),
    ForeignKeyConstraint(["result_id", "version"], ["result_versions.result_id", "result_versions.version"]),
    Index("ix_result_events_result", "result_id"),
    Index("ix_result_events_kind", "kind"),
)

report_issues = Table(
    "report_issues",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("tr_number", String(20), nullable=False, index=True),
    Column("file_sha256", String(64), nullable=False),
    Column("amends_id", ForeignKey("report_issues.id")),  # the issue this one replaces (D-20)
    *_created(),
)

report_issue_lines = Table(
    "report_issue_lines",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("issue_id", ForeignKey("report_issues.id"), nullable=False),
    Column("result_id", Integer, nullable=False),
    Column("version", Integer, nullable=False),
    ForeignKeyConstraint(["result_id", "version"], ["result_versions.result_id", "result_versions.version"]),
    UniqueConstraint("issue_id", "result_id", name="uq_report_issue_lines_line"),
)

APPEND_ONLY = tuple(metadata.tables)  # every slim-data table
