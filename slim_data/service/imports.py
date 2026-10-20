"""Data in (DESIGN.md §9, D-24, D-25, D-66): preview a file, then import it all or not at all.

`preview_import` writes nothing. It parses the file with a reader plugin, then checks what the rows
mean against the outside world (ReferenceLookup, StaffDirectory) and the stored batches, and returns
an ImportPlan: the run, any batches the file registers, every reading, and every row error.
`import_run` writes the plan only if it has no errors, so a file never lands half-imported, and the
same file (by sha256) is never imported twice.

The analyst is who the file names, checked against the host's staff and recorded as ASSERTED (a file
is a claim, not a login); `imported_by` is who uploaded it. Neither may review the results (D-66).
"""

from collections import defaultdict
from hashlib import sha256

from pydantic import ValidationError
from sqlalchemy.orm import Session

from slim_data.domain.actor import Actor
from slim_data.domain.base import Frozen
from slim_data.domain.enums import IdentitySource
from slim_data.domain.observations import PrepBatch, Reading, Run, SourceFile, SpikeAddition
from slim_data.domain.ports import ReferenceLookup, StaffDirectory
from slim_data.domain.values import AnalyteRef, Quantity
from slim_data.persistence._rows import NotFound
from slim_data.persistence.observations import (
    batch_readings,
    get_batch,
    record_run,
    register_batch,
    run_for_file,
)
from slim_data.pipeline.generic_csv import GenericCsv
from slim_data.pipeline.reader import ParsedRow, ResultReader, RowError, reader_key


class ImportPlan(Frozen):
    source: SourceFile
    run: Run | None
    new_batches: tuple[PrepBatch, ...] = ()
    readings: tuple[tuple[str, Reading], ...] = ()  # (prep batch code, reading)
    errors: tuple[RowError, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors and self.run is not None


class ImportReceipt(Frozen):
    run_id: int
    readings: int
    batches_registered: tuple[str, ...]


class ImportRefused(ValueError):
    def __init__(self, plan: ImportPlan) -> None:
        super().__init__("; ".join(str(e) for e in plan.errors) or "nothing to import")
        self.plan = plan


def preview_import(
    session: Session,
    content: bytes,
    filename: str,
    importer: Actor,
    lookup: ReferenceLookup,
    staff: StaffDirectory,
    reader: ResultReader | None = None,
) -> ImportPlan:
    reader = reader or GenericCsv()
    source = SourceFile(
        name=filename[:255] or "upload", sha256=sha256(content).hexdigest(), reader=reader_key(reader)
    )
    previous = run_for_file(session, source.sha256)
    if previous is not None:
        return ImportPlan(
            source=source, run=None, errors=(RowError(line=0, message=f"already imported as run {previous}"),)
        )
    parsed = reader.parse(content)
    errors = list(parsed.errors)
    run = None
    if parsed.run is not None:
        analyst = staff.find(parsed.run.analyst_id)
        if analyst is None:
            errors.append(
                RowError(line=0, column="analyst", message=f"no staff member {parsed.run.analyst_id!r}")
            )
        else:
            run = Run(
                run_code=parsed.run.run_code,
                instrument=parsed.run.instrument,
                analyst=analyst.model_copy(update={"identity_source": IdentitySource.ASSERTED}),
                imported_by=importer,
                analyzed_at=parsed.run.analyzed_at,
                source=source,
            )
    resolved = _resolve(parsed.rows, lookup, errors)
    new_batches, readings = _batches(session, resolved, errors)
    return ImportPlan(
        source=source,
        run=run,
        new_batches=tuple(new_batches),
        readings=tuple(readings),
        errors=tuple(sorted(errors, key=lambda e: (e.line, e.column, e.message))),
    )


def import_run(
    session: Session,
    content: bytes,
    filename: str,
    importer: Actor,
    lookup: ReferenceLookup,
    staff: StaffDirectory,
    reader: ResultReader | None = None,
) -> ImportReceipt:
    plan = preview_import(session, content, filename, importer, lookup, staff, reader)
    if not plan.ok:
        raise ImportRefused(plan)
    with session.begin_nested():  # all or nothing, even inside the caller's larger transaction
        for batch in plan.new_batches:
            register_batch(session, batch, importer)
        run = record_run(session, plan.run, plan.readings)
    return ImportReceipt(
        run_id=run.id, readings=len(plan.readings), batches_registered=tuple(b.code for b in plan.new_batches)
    )


# --- meaning of the rows ----------------------------------------------------------------------------


def _resolve(rows, lookup: ReferenceLookup, errors: list[RowError]) -> list[tuple[ParsedRow, Reading]]:
    """Each row as a Reading, checked against the reference data; rows with errors are dropped."""
    analysis_ids: dict[str, int | None] = {}
    resolved = []
    for row in rows:
        if row.analysis_code not in analysis_ids:
            analysis_ids[row.analysis_code] = lookup.analysis_id(row.analysis_code)
        analysis_id = analysis_ids[row.analysis_code]
        if analysis_id is None:
            errors.append(_error(row, "analysis_code", f"unknown analysis {row.analysis_code!r}"))
            continue
        try:
            analyte = AnalyteRef(analysis_id=analysis_id, code=row.analyte_code)
            reading = Reading(
                sample=row.sample,
                analyte=analyte,
                role=row.role,
                replicate=row.replicate,
                value=Quantity(value=row.value, unit=row.unit),
                mdl=None if row.mdl is None else Quantity(value=row.mdl, unit=row.unit),
                dilution=row.dilution,
            )
        except ValidationError as error:
            errors.append(_error(row, "", error.errors()[0]["msg"]))
            continue
        problems = []
        if not lookup.analyte_valid(analyte):
            problems.append(("analyte", f"{row.analyte_code} is not an analyte of {row.analysis_code}"))
        if row.sample is not None:
            if not lookup.sample_exists(row.sample):
                problems.append(("lab_sample_id", f"no sample {row.sample}"))
            elif not lookup.sample_requests(row.sample, analysis_id):
                problems.append(("analysis_code", f"{row.analysis_code} was not ordered for {row.sample}"))
        errors.extend(_error(row, column, message) for column, message in problems)
        if not problems:
            resolved.append((row, reading))
    return resolved


def _batches(session, resolved, errors: list[RowError]):
    """Fits the readings to their prep batches: existing ones as stored, new ones registered from the
    file's prep columns (D-24)."""
    by_batch = defaultdict(list)
    for row, reading in resolved:
        by_batch[row.prep_batch].append((row, reading))
    new_batches, readings, seen = [], [], {}
    for code, items in by_batch.items():
        try:
            existing = get_batch(session, code)
        except NotFound:
            existing = None
        stored = set()
        if existing is not None:
            stored = {_key(r) for r in batch_readings(session, existing.id)}
        good = []
        for row, reading in items:
            key = (code, *_key(reading))
            if key in seen:
                errors.append(_error(row, "replicate", f"same reading as line {seen[key]}"))
            elif _key(reading) in stored:
                errors.append(_error(row, "replicate", f"batch {code} already has this reading"))
            else:
                seen[key] = row.line
                good.append((row, reading))
        batch = _check_existing(existing, good, errors) if existing else _new_batch(code, good, errors)
        if batch is not None and existing is None:
            new_batches.append(batch)
        readings.extend((code, reading) for _, reading in good)
    return new_batches, readings


def _check_existing(batch: PrepBatch, items, errors: list[RowError]) -> PrepBatch:
    for row, reading in items:
        if reading.analyte.analysis_id != batch.analysis_id:
            errors.append(_error(row, "analysis_code", f"batch {batch.code} is for another analysis"))
        if row.prep_method is not None and row.prep_method != batch.prep_method:
            errors.append(
                _error(row, "prep_method", f"batch {batch.code} was prepped by {batch.prep_method!r}")
            )
        if reading.sample is not None and reading.sample not in batch.samples:
            errors.append(_error(row, "lab_sample_id", f"{reading.sample} is not in batch {batch.code}"))
        if row.spike_added is not None:
            stored = batch.spike_added(reading.sample, reading.analyte)
            if stored is None or stored != Quantity(value=row.spike_added, unit=row.unit):
                errors.append(
                    _error(
                        row,
                        "spike_added",
                        f"batch {batch.code} records {stored or 'no amount'} for this spike",
                    )
                )
    return batch


def _new_batch(code: str, items, errors: list[RowError]) -> PrepBatch | None:
    if not items:
        return None
    first = items[0][0]
    methods = {row.prep_method for row, _ in items if row.prep_method is not None}
    analyses = {reading.analyte.analysis_id for _, reading in items}
    if len(methods) != 1:
        message = "needs prep_method to register it" if not methods else "has more than one prep_method"
        errors.append(_error(first, "prep_method", f"new batch {code} {message}"))
        return None
    if len(analyses) != 1:
        errors.append(_error(first, "analysis_code", f"new batch {code} mixes analyses"))
        return None
    samples = list(dict.fromkeys(r.sample for _, r in items if r.sample is not None))
    amounts: dict = {}
    for row, reading in items:
        if row.spike_added is None:
            continue
        key = (reading.sample, reading.analyte)
        amount = Quantity(value=row.spike_added, unit=row.unit)
        if amounts.setdefault(key, amount) != amount:
            errors.append(
                _error(row, "spike_added", f"differs from the amount on another row ({amounts[key]})")
            )
    try:
        return PrepBatch(
            code=code,
            analysis_id=analyses.pop(),
            prep_method=methods.pop(),
            samples=tuple(samples),
            spikes=tuple(SpikeAddition(sample=s, analyte=a, added=q) for (s, a), q in amounts.items()),
        )
    except ValidationError as error:
        errors.append(_error(first, "prep_batch", f"new batch {code}: {error.errors()[0]['msg']}"))
        return None


def _key(reading: Reading) -> tuple:
    return (reading.sample, reading.analyte, reading.role, reading.replicate)


def _error(row: ParsedRow, column: str, message: str) -> RowError:
    return RowError(line=row.line, column=column, message=message)
