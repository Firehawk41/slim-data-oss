"""Each persistence test runs on a freshly migrated schema inside one transaction that is rolled back
afterwards (Postgres and SQLite both roll back DDL), so tests never see each other's rows."""

import time
from collections.abc import Iterator
from datetime import UTC, datetime
from hashlib import sha256

import pytest
from sqlalchemy.orm import Session

from slim_data.domain.enums import ReadingRole
from slim_data.domain.observations import PrepBatch, Reading, Run, SourceFile, SpikeAddition
from slim_data.persistence import migrate
from slim_data.persistence._rows import database_time
from slim_data.persistence.observations import record_run, register_batch
from slim_data.persistence.rulesets import publish_ruleset
from tests.engine.helpers import DEMO
from tests.factories import ANA, analyte, qty, sample


@pytest.fixture
def session(engine) -> Iterator[Session]:
    with engine.connect() as connection:
        transaction = connection.begin()
        migrate.upgrade(connection)
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()


def make_run(name: str = "run1.csv") -> Run:
    return Run(
        run_code="RUN-0042",
        instrument="ICP-MS 1",
        analyst=ANA,
        analyzed_at=datetime(2026, 10, 19, 9, 30, tzinfo=UTC),
        source=SourceFile(name=name, sha256=sha256(name.encode()).hexdigest(), reader="generic_csv/1"),
    )


def make_batch(code: str = "PB-0001") -> PrepBatch:
    return PrepBatch(
        code=code,
        analysis_id=1,
        prep_method="microwave HNO3",
        samples=(sample(1), sample(2)),
        spikes=(SpikeAddition(sample=sample(1), analyte=analyte("Cu"), added=qty("20")),),
    )


def make_reading(pos, role, replicate, value, code="Cu", mdl="0.05", analysis_id=1) -> Reading:
    return Reading(
        sample=None if pos is None else sample(pos),
        analyte=analyte(code, analysis_id),
        role=role,
        replicate=replicate,
        value=qty(value),
        mdl=None if mdl is None else qty(mdl),
    )


def standard_readings() -> list[Reading]:
    """Sample 1: cups 10.00 / 10.20 and a spike; sample 2: cups 5.10 / 5.30; one blank."""
    return [
        make_reading(1, ReadingRole.CUP, 1, "10.00"),
        make_reading(1, ReadingRole.CUP, 2, "10.20"),
        make_reading(1, ReadingRole.SPIKE, 1, "30.0"),
        make_reading(2, ReadingRole.CUP, 1, "5.10"),
        make_reading(2, ReadingRole.CUP, 2, "5.30"),
        make_reading(None, ReadingRole.BLANK, 1, "0.010"),
    ]


@pytest.fixture
def stored(session):
    """A published demo ruleset, one batch and one run of readings."""
    ruleset = publish_ruleset(session, DEMO, ANA)
    batch = register_batch(session, make_batch(), ANA)
    run = record_run(session, make_run(), [(batch.code, r) for r in standard_readings()])
    return ruleset, batch, run


def instant(session):
    """A moment between two writes. `as_of` resolves to the database clock's tick (microseconds on
    Postgres, milliseconds on SQLite), so step clear of the tick on either side."""
    time.sleep(0.002)
    moment = database_time(session)
    time.sleep(0.002)
    return moment
