"""Column types, the database clock, append-only triggers (D-04, D-21) and the audit context (D-65)."""

import time
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, delete, insert, select, text, update
from sqlalchemy.exc import DBAPIError, StatementError

from slim_data.domain.enums import EventKind, ReadingRole
from slim_data.domain.observations import ReadingExclusion
from slim_data.domain.policy import DEMO_POLICY
from slim_data.domain.results import ResultEvent
from slim_data.persistence import audit
from slim_data.persistence._rows import as_of_filter, database_time
from slim_data.persistence.observations import exclude_reading
from slim_data.persistence.results import add_event, add_version
from slim_data.persistence.rulesets import publish_policy
from slim_data.persistence.tables import APPEND_ONLY, readings, report_issue_lines, report_issues
from slim_data.persistence.types import ExactDecimal, UtcDateTime
from tests.factories import ANA, ROB, analyte, sample, version


def scratch(session):
    table = Table(
        "scratch",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("d", ExactDecimal()),
        Column("t", UtcDateTime()),
    )
    table.create(session.connection())
    return table


def test_decimals_keep_their_digits_and_floats_are_refused(session):
    table = scratch(session)
    session.execute(insert(table).values(id=1, d=Decimal("12.40")))
    assert str(session.scalar(select(table.c.d))) == "12.40"
    with pytest.raises(StatementError, match="expected a Decimal"):
        session.execute(insert(table).values(id=2, d=12.4))


def test_datetimes_are_aware_utc(session):
    table = scratch(session)
    plus_two = datetime(2026, 10, 20, 14, 0, tzinfo=timezone(timedelta(hours=2)))
    session.execute(insert(table).values(id=1, t=plus_two))
    stored = session.scalar(select(table.c.t))
    assert stored == plus_two and stored.tzinfo == UTC
    with pytest.raises(StatementError, match="timezone-aware"):
        session.execute(insert(table).values(id=2, t=datetime(2026, 10, 20)))
    with pytest.raises(ValueError, match="timezone-aware"):
        as_of_filter(table.c.t, datetime(2026, 10, 20))


def test_the_database_clock_moves_within_one_transaction(session):
    first = database_time(session)
    time.sleep(0.005)  # SQLite's clock has millisecond resolution; ties are broken by row id
    assert database_time(session) > first and first.tzinfo == UTC


@pytest.fixture
def every_table(session, stored):
    """At least one row in every slim-data table."""
    ruleset, batch, _ = stored
    reading_id = session.scalar(select(readings.c.id).where(readings.c.role == int(ReadingRole.CUP)))
    exclude_reading(session, ReadingExclusion(reading_id=reading_id, reason="bubble", actor=ANA))
    add_version(session, sample(1), analyte("Cu"), version(1, ruleset_id=ruleset.id), batch.id)
    add_event(session, sample(1), analyte("Cu"), ResultEvent(kind=EventKind.REVIEWED, version=1, actor=ROB))
    issue_id = session.execute(
        insert(report_issues).values(
            tr_number="TR00042", file_sha256="0" * 64, created_by_id="rob", created_by={"id": "rob"}
        )
    ).inserted_primary_key[0]
    session.execute(insert(report_issue_lines).values(issue_id=issue_id, result_id=1, version=1))
    publish_policy(session, DEMO_POLICY, ANA)


@pytest.mark.parametrize("table_name", APPEND_ONLY)
def test_every_table_refuses_update_and_delete(session, every_table, table_name):
    table = readings.metadata.tables[table_name]
    assert session.execute(select(table)).first() is not None, f"{table_name} has no row to test with"
    first_column = next(iter(table.c))
    for statement in (update(table).values({first_column.name: first_column}), delete(table)):
        with pytest.raises(DBAPIError, match="append-only"):
            with session.begin_nested():
                session.execute(statement)


def test_postgres_also_refuses_truncate(session, every_table):
    if session.get_bind().dialect.name != "postgresql":
        pytest.skip("TRUNCATE is Postgres-only")
    with pytest.raises(DBAPIError, match="append-only"):
        with session.begin_nested():
            session.execute(text("TRUNCATE readings CASCADE"))


def test_change_context_is_set_for_the_audit_log(session):
    audit.set_change_context(session, ANA, "import run1.csv")
    if session.get_bind().dialect.name != "postgresql":
        return  # no audit log on SQLite: nothing to set
    settings = session.execute(
        text("SELECT current_setting(:a, true), current_setting(:b, true)"),
        {"a": audit.CHANGED_BY, "b": audit.CHANGE_REASON},
    ).one()
    assert tuple(settings) == ("ana", "import run1.csv")


def test_setting_names_match_slim_domain():
    slim_audit = pytest.importorskip("infrastructure.audit")
    source = open(slim_audit.__file__).read()
    assert f"'{audit.CHANGED_BY}'" in source and f"'{audit.CHANGE_REASON}'" in source
