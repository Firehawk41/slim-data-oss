"""What was observed (DESIGN.md §6.1): prep batches, runs with their readings, and exclusions.

Everything is a fact once written: a reading is never edited; an analyst who distrusts it adds an
exclusion. `evaluation_input` reads a batch back, as of any past moment, in the shape the engine
takes.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from slim_data.domain.actor import Actor
from slim_data.domain.enums import ExclusionKind, ReadingRole, SourceKind
from slim_data.domain.observations import (
    Calibration,
    CupDilution,
    PrepBatch,
    QcExpectation,
    Reading,
    ReadingExclusion,
    Run,
    SourceFile,
    SpikeAddition,
)
from slim_data.domain.values import AnalyteRef, Quantity, SampleRef
from slim_data.engine.model import EvaluationInput
from slim_data.persistence._rows import NotFound, actor_json, as_of_filter
from slim_data.persistence.audit import set_change_context
from slim_data.persistence.tables import (
    prep_batch_dilutions,
    prep_batch_qc,
    prep_batch_samples,
    prep_batch_spikes,
    prep_batches,
    reading_exclusions,
    readings,
    run_calibrations,
    run_files,
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
    _insert_members(session, batch_id, batch.samples, batch.spikes, batch.qc)
    if batch.dilutions:
        session.execute(
            prep_batch_dilutions.insert(),
            [
                {
                    "batch_id": batch_id,
                    "tr_number": d.sample.tr_number,
                    "position": d.sample.position,
                    "role": int(d.role),
                    "replicate": d.replicate,
                    "dilution": d.dilution,
                }
                for d in batch.dilutions
            ],
        )
    return batch.model_copy(update={"id": batch_id})


def add_to_batch(
    session: Session,
    code: str,
    samples: Sequence[SampleRef],
    spikes: Sequence[SpikeAddition],
    actor: Actor,
    qc: Sequence[QcExpectation] = (),
) -> PrepBatch:
    """Adds samples, spike amounts and QC expected values to a registered batch: new rows, never
    changes (a file exported while the run is still going registers the batch with only the samples
    it has so far, D-66)."""
    batch = get_batch(session, code)
    clash = (
        [s for s in samples if s in batch.samples]
        + [
            f"{s.sample} {s.analyte.code}"
            for s in spikes
            if batch.spike_added(s.sample, s.analyte) is not None
        ]
        + [f"{q.role.name} {q.analyte.code}" for q in qc if batch.qc_expected(q.role, q.analyte) is not None]
    )
    if clash:
        raise ValueError(f"batch {code} already has {', '.join(map(str, clash))}")
    set_change_context(session, actor, f"add to prep batch {code}")
    _insert_members(session, batch.id, samples, spikes, qc)
    return get_batch(session, code)


def _insert_members(session, batch_id, samples, spikes, qc=()) -> None:
    if samples:
        session.execute(
            prep_batch_samples.insert(),
            [{"batch_id": batch_id, "tr_number": s.tr_number, "position": s.position} for s in samples],
        )
    if spikes:
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
                for s in spikes
            ],
        )
    if qc:
        session.execute(
            prep_batch_qc.insert(),
            [
                {
                    "batch_id": batch_id,
                    "role": int(q.role),
                    "analysis_id": q.analyte.analysis_id,
                    "analyte_code": q.analyte.code,
                    "expected": q.expected.value,
                    "unit": q.expected.unit,
                    "material": q.material,
                    "lot": q.lot,
                }
                for q in qc
            ],
        )


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
        .where(prep_batch_samples.c.batch_id == row.id, as_of_filter(prep_batch_samples.c.created_at, as_of))
        .order_by(prep_batch_samples.c.id)
    ).all()
    spikes = _latest(
        session.execute(
            select(prep_batch_spikes)
            .where(
                prep_batch_spikes.c.batch_id == row.id, as_of_filter(prep_batch_spikes.c.created_at, as_of)
            )
            .order_by(prep_batch_spikes.c.id)
        ).all(),
        lambda s: (s.tr_number, s.position, s.analysis_id, s.analyte_code),
    )
    qc = session.execute(
        select(prep_batch_qc)
        .where(prep_batch_qc.c.batch_id == row.id, as_of_filter(prep_batch_qc.c.created_at, as_of))
        .order_by(prep_batch_qc.c.id)
    ).all()
    dilutions = _latest(
        session.execute(
            select(prep_batch_dilutions)
            .where(
                prep_batch_dilutions.c.batch_id == row.id,
                as_of_filter(prep_batch_dilutions.c.created_at, as_of),
            )
            .order_by(prep_batch_dilutions.c.id)
        ).all(),
        lambda d: (d.tr_number, d.position, d.role, d.replicate),
    )
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
        qc=tuple(
            QcExpectation(
                role=ReadingRole(q.role),
                analyte=AnalyteRef(analysis_id=q.analysis_id, code=q.analyte_code),
                expected=Quantity(value=q.expected, unit=q.unit),
                material=q.material,
                lot=q.lot,
            )
            for q in qc
        ),
        dilutions=tuple(
            CupDilution(
                sample=SampleRef(tr_number=d.tr_number, position=d.position),
                role=ReadingRole(d.role),
                replicate=d.replicate,
                dilution=d.dilution,
            )
            for d in dilutions
        ),
    )


def _latest(rows, key) -> list:
    """The newest version of each entry, in the order the entries were first recorded (D-96)."""
    newest: dict = {}
    for row in rows:
        k = key(row)
        if k not in newest or row.version > newest[k].version:
            newest[k] = row
    return list(newest.values())


def correct_prep(
    session: Session,
    code: str,
    actor: Actor,
    reason: str,
    dilutions: Sequence[CupDilution] = (),
    spikes: Sequence[SpikeAddition] = (),
) -> tuple[PrepBatch, int]:
    """A prep correction after handoff (D-96): each changed cup dilution or spike amount becomes the next
    version of that entry, with why and who. Entries must already be on the batch (new ones go through
    `add_to_batch`). Returns the batch as it now stands and how many entries changed. The host
    re-evaluates the batch afterwards; released results it changes go through a correction (D-69)."""
    reason = reason.strip()
    if not reason:
        raise ValueError("a prep correction needs a reason")
    batch = get_batch(session, code)
    changes = []
    for d in dilutions:
        current = batch.prep_dilution(d.sample, d.role, d.replicate)
        if current is None:
            raise ValueError(f"batch {code} has no prep dilution for {d.sample} {d.role.name} {d.replicate}")
        if current != d.dilution:
            key = (
                prep_batch_dilutions,
                {"tr_number": d.sample.tr_number, "position": d.sample.position, "role": int(d.role),
                 "replicate": d.replicate},
            )  # fmt: skip
            changes.append((*key, {"dilution": d.dilution}))
    for s in spikes:
        current = batch.spike_added(s.sample, s.analyte)
        if current is None:
            raise ValueError(f"batch {code} has no spike amount for {s.sample} {s.analyte.code}")
        if current != s.added:
            key = (
                prep_batch_spikes,
                {"tr_number": s.sample.tr_number, "position": s.sample.position,
                 "analysis_id": s.analyte.analysis_id, "analyte_code": s.analyte.code},
            )  # fmt: skip
            changes.append((*key, {"added": s.added.value, "unit": s.added.unit}))
    if changes:
        set_change_context(session, actor, reason)
    for table, key, values in changes:
        latest = session.scalar(
            select(table.c.version)
            .where(table.c.batch_id == batch.id, *(table.c[c] == v for c, v in key.items()))
            .order_by(table.c.version.desc())
            .limit(1)
        )
        session.execute(
            table.insert().values(
                batch_id=batch.id, **key, **values, version=latest + 1, reason=reason, created_by_id=actor.id
            )
        )
    return get_batch(session, code), len(changes)


def list_batches(session: Session, as_of: datetime | None = None) -> list:
    """Every prep batch's code, analysis, prep method, registration time and whether it has samples,
    newest first."""
    has_samples = exists().where(prep_batch_samples.c.batch_id == prep_batches.c.id).label("has_samples")
    return session.execute(
        select(
            prep_batches.c.code,
            prep_batches.c.analysis_id,
            prep_batches.c.prep_method,
            prep_batches.c.created_at,
            has_samples,
        )
        .where(as_of_filter(prep_batches.c.created_at, as_of))
        .order_by(prep_batches.c.created_at.desc(), prep_batches.c.id.desc())
    ).all()


def run_for_file(session: Session, sha256: str) -> int | None:
    """The run a file was imported as, if it was."""
    return session.scalar(select(runs.c.id).where(runs.c.source_sha256 == sha256))


def record_run(
    session: Session,
    run: Run,
    batch_readings: Sequence[tuple[str, Reading]],
    content: bytes | None = None,
    calibrations: Sequence[Calibration] = (),
) -> tuple[Run, tuple[int, ...]]:
    """Stores one run and its readings, each tagged with its prep batch's code, the file itself when
    given (D-88) and the run's calibration (D-89); returns the run and the readings' ids, in order.
    Refuses a file that was imported before, and readings that don't fit their batch."""
    previous = run_for_file(session, run.source.sha256)
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
        if reading.role.is_qc:
            expected = batch.qc_expected(reading.role, reading.analyte)
            if expected is None or expected.expected.unit != reading.value.unit:
                raise ValueError(
                    f"batch {code} has no expected value in {reading.value.unit} "
                    f"for the {reading.role.name} {reading.analyte.code}"
                )
    set_change_context(session, run.imported_by, f"import {run.source.name}")
    run_id = session.execute(
        runs.insert().values(
            run_code=run.run_code,
            instrument=run.instrument,
            analyzed_at=run.analyzed_at,
            source_name=run.source.name,
            source_sha256=run.source.sha256,
            reader=run.source.reader,
            source_kind=int(run.source.kind),
            analyst_id=run.analyst.id,
            analyst=actor_json(run.analyst),
            imported_by_id=run.imported_by.id,
            imported_by=actor_json(run.imported_by),
        )
    ).inserted_primary_key[0]
    if content is not None:
        session.execute(run_files.insert().values(run_id=run_id, content=content))
    for c in calibrations:
        session.execute(
            run_calibrations.insert().values(
                run_id=run_id,
                analysis_id=c.analyte.analysis_id,
                analyte_code=c.analyte.code,
                unit=c.unit,
                correlation=c.correlation,
                determination=c.determination,
                lod=c.lod,
                bec=c.bec,
                conditions=c.conditions,
            )
        )
    rows = [
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
            "rsd": r.rsd,
            "istd_recovery": r.istd_recovery,
            "attempt": r.attempt,
            "sequence": r.sequence,
            "conditions": r.conditions,
        }
        for code, r in batch_readings
    ]
    ids = tuple(session.execute(readings.insert().values(row)).inserted_primary_key[0] for row in rows)
    return run.model_copy(update={"id": run_id}), ids


def get_run(session: Session, run_id: int) -> Run:
    row = session.execute(select(runs).where(runs.c.id == run_id)).one_or_none()
    if row is None:
        raise NotFound(f"no run {run_id}")
    return Run(
        id=row.id,
        run_code=row.run_code,
        instrument=row.instrument,
        analyst=Actor.model_validate(row.analyst),
        imported_by=Actor.model_validate(row.imported_by or row.analyst),
        analyzed_at=row.analyzed_at,
        source=SourceFile(
            name=row.source_name,
            sha256=row.source_sha256,
            reader=row.reader,
            kind=SourceKind(row.source_kind),
        ),
    )


def calibrations(session: Session, run_id: int) -> tuple[Calibration, ...]:
    """A run's calibration per analyte, as the instrument reported it (D-89)."""
    return _calibrations(session, run_calibrations.c.run_id == run_id)


def _calibrations(session: Session, which) -> tuple[Calibration, ...]:
    rows = session.execute(select(run_calibrations).where(which).order_by(run_calibrations.c.id))
    return tuple(
        Calibration(
            analyte=AnalyteRef(analysis_id=row.analysis_id, code=row.analyte_code),
            run_id=row.run_id,
            unit=row.unit,
            correlation=row.correlation,
            determination=row.determination,
            lod=row.lod,
            bec=row.bec,
            conditions=row.conditions,
        )
        for row in rows
    )


def run_file(session: Session, run_id: int) -> bytes | None:
    """The file a run was imported from, as it was; None for runs imported before 0006 (D-88)."""
    return session.scalar(select(run_files.c.content).where(run_files.c.run_id == run_id))


def exclude_reading(session: Session, exclusion: ReadingExclusion) -> ReadingExclusion:
    """Takes a reading out (EXCLUDED) or puts it back (RESTORED). Results already decided don't
    change; the next explicit re-evaluation reads the new state (D-12)."""
    if session.scalar(select(readings.c.id).where(readings.c.id == exclusion.reading_id)) is None:
        raise NotFound(f"no reading {exclusion.reading_id}")
    currently_excluded = exclusion.reading_id in _excluded(
        session, readings.c.id == exclusion.reading_id, None
    )
    if exclusion.kind == ExclusionKind.EXCLUDED and currently_excluded:
        raise ValueError(f"reading {exclusion.reading_id} is already excluded")
    if exclusion.kind == ExclusionKind.RESTORED and not currently_excluded:
        raise ValueError(f"reading {exclusion.reading_id} is not excluded, so there is nothing to restore")
    set_change_context(session, exclusion.actor, exclusion.reason)
    exclusion_id = session.execute(
        reading_exclusions.insert().values(
            reading_id=exclusion.reading_id,
            kind=int(exclusion.kind),
            reason=exclusion.reason,
            created_by_id=exclusion.actor.id,
            created_by=actor_json(exclusion.actor),
        )
    ).inserted_primary_key[0]
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
    found = tuple(with_prep_dilution(batch, r) for r in found)
    excluded = _excluded(session, readings.c.batch_id == batch.id, as_of)
    runs_used = {r.run_id for r in found}
    found_calibrations = _calibrations(session, run_calibrations.c.run_id.in_(runs_used)) if runs_used else ()
    codes = session.execute(select(runs.c.id, runs.c.run_code).where(runs.c.id.in_(runs_used))).all()
    return EvaluationInput(
        batch=batch,
        readings=found,
        excluded_reading_ids=excluded,
        calibrations=found_calibrations,
        run_codes=tuple(sorted((r.id, r.run_code) for r in codes)),
    )


def with_prep_dilution(batch: PrepBatch, reading: Reading) -> Reading:
    """The prep book's dilution as it stood (D-96): a correction after import reaches the readings
    without editing them. Readings keep the dilution they were imported with."""
    if reading.sample is None:
        return reading
    prep = batch.prep_dilution(reading.sample, reading.role, reading.replicate)
    return (
        reading if prep is None or prep == reading.dilution else reading.model_copy(update={"dilution": prep})
    )


def _excluded(session: Session, which, as_of: datetime | None) -> frozenset[int]:
    """Readings whose newest exclusion row (as of `as_of`) says EXCLUDED."""
    rows = session.execute(
        select(reading_exclusions.c.reading_id, reading_exclusions.c.kind)
        .join(readings, readings.c.id == reading_exclusions.c.reading_id)
        .where(which, as_of_filter(reading_exclusions.c.created_at, as_of))
        .order_by(reading_exclusions.c.created_at, reading_exclusions.c.id)
    ).all()
    latest = {row.reading_id: row.kind for row in rows}
    return frozenset(reading for reading, kind in latest.items() if kind == ExclusionKind.EXCLUDED)


def _reading(row) -> Reading:
    return Reading(
        id=row.id,
        run_id=row.run_id,
        sample=None if row.tr_number is None else SampleRef(tr_number=row.tr_number, position=row.position),
        analyte=AnalyteRef(analysis_id=row.analysis_id, code=row.analyte_code),
        role=ReadingRole(row.role),
        replicate=row.replicate,
        attempt=row.attempt,
        value=Quantity(value=row.value, unit=row.unit),
        mdl=None if row.mdl is None else Quantity(value=row.mdl, unit=row.unit),
        dilution=row.dilution,
        rsd=row.rsd,
        istd_recovery=row.istd_recovery,
        sequence=row.sequence,
        conditions=row.conditions,
    )


def entered_by(session: Session, batch_id: int) -> dict[tuple[SampleRef, AnalyteRef], frozenset[str]]:
    """For each sample × analyte of a batch, everyone who put its readings in: the analyst each file
    named and whoever uploaded it (D-62, D-66). Four-eyes keeps them all off the review."""
    rows = session.execute(
        select(
            readings.c.tr_number,
            readings.c.position,
            readings.c.analysis_id,
            readings.c.analyte_code,
            runs.c.analyst_id,
            runs.c.imported_by_id,
        )
        .join(runs, runs.c.id == readings.c.run_id)
        .where(readings.c.batch_id == batch_id, readings.c.tr_number.isnot(None))
    ).all()
    people: dict = {}
    for row in rows:
        key = (
            SampleRef(tr_number=row.tr_number, position=row.position),
            AnalyteRef(analysis_id=row.analysis_id, code=row.analyte_code),
        )
        people.setdefault(key, set()).update(p for p in (row.analyst_id, row.imported_by_id) if p)
    return {key: frozenset(ids) for key, ids in people.items()}


def stored_with_runs(session: Session, batch_id: int) -> list[tuple[Reading, str, bool]]:
    """Every reading of a batch with its run's code and whether it is excluded now, oldest first:
    what an import compares a re-exported file against (D-66)."""
    rows = session.execute(
        select(readings, runs.c.run_code)
        .join(runs, runs.c.id == readings.c.run_id)
        .where(readings.c.batch_id == batch_id)
        .order_by(readings.c.id)
    ).all()
    excluded = _excluded(session, readings.c.batch_id == batch_id, None)
    return [(_reading(row), row.run_code, row.id in excluded) for row in rows]


def qc_history(
    session: Session, since: datetime | None = None, as_of: datetime | None = None
) -> tuple[list, frozenset[int], list]:
    """Every BLANK, CHECK and CRM reading with its run and batch, the excluded ones' ids, and every
    run's calibration with its run, oldest run first: what the quality charts read (D-107)."""
    batch_roles = [int(r) for r in ReadingRole if r.of_batch]
    when = [as_of_filter(readings.c.created_at, as_of)]
    if since is not None:
        when.append(runs.c.analyzed_at >= since)
    found = session.execute(
        select(
            readings,
            runs.c.run_code,
            runs.c.instrument,
            runs.c.analyzed_at,
            runs.c.analyst_id,
            prep_batches.c.code.label("batch_code"),
        )
        .join(runs, runs.c.id == readings.c.run_id)
        .join(prep_batches, prep_batches.c.id == readings.c.batch_id)
        .where(readings.c.role.in_(batch_roles), *when)
        .order_by(runs.c.analyzed_at, readings.c.run_id, readings.c.sequence, readings.c.id)
    ).all()
    excluded = _excluded(session, readings.c.role.in_(batch_roles), as_of)
    cal_when = [as_of_filter(run_calibrations.c.created_at, as_of)]
    if since is not None:
        cal_when.append(runs.c.analyzed_at >= since)
    cals = session.execute(
        select(run_calibrations, runs.c.run_code, runs.c.instrument, runs.c.analyzed_at)
        .join(runs, runs.c.id == run_calibrations.c.run_id)
        .where(*cal_when)
        .order_by(runs.c.analyzed_at, run_calibrations.c.run_id, run_calibrations.c.id)
    ).all()
    return found, excluded, cals
