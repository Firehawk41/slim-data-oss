"""What was observed (DESIGN.md §6.1): prep batches, runs with their readings, and exclusions.

Everything is a fact once written: a reading is never edited; an analyst who distrusts it adds an
exclusion. `evaluation_input` reads a batch back, as of any past moment, in the shape the engine
takes.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from slim_data.domain.actor import Actor
from slim_data.domain.enums import ReadingRole
from slim_data.domain.observations import PrepBatch, Reading, ReadingExclusion, Run, SourceFile, SpikeAddition
from slim_data.domain.values import AnalyteRef, Quantity, SampleRef
from slim_data.engine.model import EvaluationInput
from slim_data.persistence._rows import NotFound, actor_json, as_of_filter
from slim_data.persistence.audit import set_change_context
from slim_data.persistence.tables import (
    prep_batch_samples,
    prep_batch_spikes,
    prep_batches,
    reading_exclusions,
    readings,
    runs,
)


class AlreadyImported(ValueError):
    """The same file (by sha256) was imported before (DESIGN.md §9)."""

    def __init__(self, run_id: int) -> None:
        super().__init__(f"this file was already imported as run {run_id}")
        self.run_id = run_id


def register_batch(session: Session, batch: PrepBatch, actor: Actor) -> PrepBatch:
    set_change_context(session, actor, f"register prep batch {batch.code}")
    try:
        with session.begin_nested():
            batch_id = session.execute(
                prep_batches.insert().values(
                    code=batch.code,
                    analysis_id=batch.analysis_id,
                    prep_method=batch.prep_method,
                    created_by_id=actor.id,
                    created_by=actor_json(actor),
                )
            ).inserted_primary_key[0]
    except IntegrityError:
        raise ValueError(f"prep batch {batch.code} already exists") from None
    session.execute(
        prep_batch_samples.insert(),
        [{"batch_id": batch_id, "tr_number": s.tr_number, "position": s.position} for s in batch.samples],
    )
    if batch.spikes:
        session.execute(
            prep_batch_spikes.insert(),
            [
                {
                    "batch_id": batch_id,
                    "tr_number": s.sample.tr_number,
                    "position": s.sample.position,
                    "analysis_id": s.analyte.analysis_id,
                    "analyte_code": s.analyte.code,
                    "added": s.added.value,
                    "unit": s.added.unit,
                }
                for s in batch.spikes
            ],
        )
    return batch.model_copy(update={"id": batch_id})


def get_batch(session: Session, code: str, as_of: datetime | None = None) -> PrepBatch:
    row = session.execute(
        select(prep_batches).where(
            prep_batches.c.code == code, as_of_filter(prep_batches.c.created_at, as_of)
        )
    ).one_or_none()
    if row is None:
        raise NotFound(f"no prep batch {code}")
    samples = session.execute(
        select(prep_batch_samples)
        .where(prep_batch_samples.c.batch_id == row.id)
        .order_by(prep_batch_samples.c.id)
    ).all()
    spikes = session.execute(
        select(prep_batch_spikes)
        .where(prep_batch_spikes.c.batch_id == row.id)
        .order_by(prep_batch_spikes.c.id)
    ).all()
    return PrepBatch(
        id=row.id,
        code=row.code,
        analysis_id=row.analysis_id,
        prep_method=row.prep_method,
        samples=tuple(SampleRef(tr_number=s.tr_number, position=s.position) for s in samples),
        spikes=tuple(
            SpikeAddition(
                sample=SampleRef(tr_number=s.tr_number, position=s.position),
                analyte=AnalyteRef(analysis_id=s.analysis_id, code=s.analyte_code),
                added=Quantity(value=s.added, unit=s.unit),
            )
            for s in spikes
        ),
    )


def record_run(session: Session, run: Run, batch_readings: Sequence[tuple[str, Reading]]) -> Run:
    """Stores one run and its readings, each tagged with its prep batch's code. Refuses a file that
    was imported before, and readings that don't fit their batch."""
    previous = session.scalar(select(runs.c.id).where(runs.c.source_sha256 == run.source.sha256))
    if previous is not None:
        raise AlreadyImported(previous)
    if not batch_readings:
        raise ValueError("a run needs at least one reading")
    batches = {code: get_batch(session, code) for code in {code for code, _ in batch_readings}}
    for code, reading in batch_readings:
        batch = batches[code]
        if reading.analyte.analysis_id != batch.analysis_id:
            raise ValueError(f"reading for {reading.analyte}, which is not batch {code}'s analysis")
        if reading.sample is not None and reading.sample not in batch.samples:
            raise ValueError(f"reading for {reading.sample}, which is not in batch {code}")
    set_change_context(session, run.analyst, f"import {run.source.name}")
    run_id = session.execute(
        runs.insert().values(
            run_code=run.run_code,
            instrument=run.instrument,
            analyzed_at=run.analyzed_at,
            source_name=run.source.name,
            source_sha256=run.source.sha256,
            reader=run.source.reader,
            analyst_id=run.analyst.id,
            analyst=actor_json(run.analyst),
        )
    ).inserted_primary_key[0]
    session.execute(
        readings.insert(),
        [
            {
                "run_id": run_id,
                "batch_id": batches[code].id,
                "tr_number": r.sample.tr_number if r.sample else None,
                "position": r.sample.position if r.sample else None,
                "analysis_id": r.analyte.analysis_id,
                "analyte_code": r.analyte.code,
                "role": int(r.role),
                "replicate": r.replicate,
                "value": r.value.value,
                "unit": r.value.unit,
                "mdl": r.mdl.value if r.mdl else None,
                "dilution": r.dilution,
            }
            for code, r in batch_readings
        ],
    )
    return run.model_copy(update={"id": run_id})


def get_run(session: Session, run_id: int) -> Run:
    row = session.execute(select(runs).where(runs.c.id == run_id)).one_or_none()
    if row is None:
        raise NotFound(f"no run {run_id}")
    return Run(
        id=row.id,
        run_code=row.run_code,
        instrument=row.instrument,
        analyst=Actor.model_validate(row.analyst),
        analyzed_at=row.analyzed_at,
        source=SourceFile(name=row.source_name, sha256=row.source_sha256, reader=row.reader),
    )


def exclude_reading(session: Session, exclusion: ReadingExclusion) -> ReadingExclusion:
    role = session.scalar(select(readings.c.role).where(readings.c.id == exclusion.reading_id))
    if role is None:
        raise NotFound(f"no reading {exclusion.reading_id}")
    if role == ReadingRole.SPIKE:
        raise ValueError("a spike can't be excluded: only cups and blanks")
    set_change_context(session, exclusion.actor, exclusion.reason)
    try:
        with session.begin_nested():
            exclusion_id = session.execute(
                reading_exclusions.insert().values(
                    reading_id=exclusion.reading_id,
                    reason=exclusion.reason,
                    created_by_id=exclusion.actor.id,
                    created_by=actor_json(exclusion.actor),
                )
            ).inserted_primary_key[0]
    except IntegrityError:
        raise ValueError(f"reading {exclusion.reading_id} is already excluded") from None
    at = session.scalar(
        select(reading_exclusions.c.created_at).where(reading_exclusions.c.id == exclusion_id)
    )
    return exclusion.model_copy(update={"at": at})


def batch_readings(session: Session, batch_id: int, as_of: datetime | None = None) -> tuple[Reading, ...]:
    rows = session.execute(
        select(readings)
        .where(readings.c.batch_id == batch_id, as_of_filter(readings.c.created_at, as_of))
        .order_by(readings.c.id)
    ).all()
    return tuple(_reading(row) for row in rows)


def evaluation_input(session: Session, code: str, as_of: datetime | None = None) -> EvaluationInput:
    """A batch's readings and exclusions as they stood at `as_of`, ready for the engine. Spec limits
    come from the host (DESIGN.md §5) and are added by the caller."""
    batch = get_batch(session, code, as_of)
    found = batch_readings(session, batch.id, as_of)
    if not found:
        raise NotFound(f"prep batch {code} has no readings yet")
    excluded = session.scalars(
        select(reading_exclusions.c.reading_id)
        .join(readings, readings.c.id == reading_exclusions.c.reading_id)
        .where(readings.c.batch_id == batch.id, as_of_filter(reading_exclusions.c.created_at, as_of))
    ).all()
    return EvaluationInput(batch=batch, readings=found, excluded_reading_ids=frozenset(excluded))


def _reading(row) -> Reading:
    return Reading(
        id=row.id,
        sample=None if row.tr_number is None else SampleRef(tr_number=row.tr_number, position=row.position),
        analyte=AnalyteRef(analysis_id=row.analysis_id, code=row.analyte_code),
        role=ReadingRole(row.role),
        replicate=row.replicate,
        value=Quantity(value=row.value, unit=row.unit),
        mdl=None if row.mdl is None else Quantity(value=row.mdl, unit=row.unit),
        dilution=row.dilution,
    )
