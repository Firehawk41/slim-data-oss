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
from dataclasses import dataclass, field
from hashlib import sha256

from pydantic import ValidationError
from sqlalchemy.orm import Session

from slim_data.domain.actor import Actor
from slim_data.domain.base import Frozen
from slim_data.domain.enums import IdentitySource, SourceKind
from slim_data.domain.observations import (
    Calibration,
    PrepBatch,
    QcExpectation,
    Reading,
    ReadingExclusion,
    Run,
    SourceFile,
    SpikeAddition,
)
from slim_data.domain.ports import ReferenceLookup, StaffDirectory
from slim_data.domain.values import AnalyteRef, Quantity, SampleRef
from slim_data.persistence._rows import NotFound
from slim_data.persistence.observations import (
    add_to_batch,
    exclude_reading,
    get_batch,
    record_run,
    register_batch,
    run_for_file,
    stored_with_runs,
    with_prep_dilution,
)
from slim_data.persistence.rulesets import current_policy
from slim_data.pipeline.generic_csv import GenericCsv
from slim_data.pipeline.reader import ParsedRow, ResultReader, RowError, reader_key


class BatchAddition(Frozen):
    """Samples, spike amounts and QC expected values a file adds to a batch registered earlier (D-66)."""

    code: str
    samples: tuple[SampleRef, ...] = ()
    spikes: tuple[SpikeAddition, ...] = ()
    qc: tuple[QcExpectation, ...] = ()


class ImportPlan(Frozen):
    source: SourceFile
    run: Run | None
    new_batches: tuple[PrepBatch, ...] = ()
    batch_additions: tuple[BatchAddition, ...] = ()
    readings: tuple[tuple[str, Reading], ...] = ()  # new readings: (prep batch code, reading)
    rejected: tuple[tuple[int, str], ...] = ()  # (index into readings, reason): rejected in the run
    rejected_stored: tuple[tuple[int, str], ...] = ()  # (reading id, reason): rejected since last import
    skipped: int = 0  # rows imported before (a re-export of a growing file)
    calibrations: tuple[Calibration, ...] = ()  # the run's, per analyte of the analysis (D-89)
    ignored: tuple[RowError, ...] = ()  # left out on purpose, shown so nothing vanishes silently
    errors: tuple[RowError, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors and self.run is not None


class ImportReceipt(Frozen):
    run_id: int | None  # None when the file held nothing new
    readings: int
    skipped: int
    rejected: int
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
        name=filename[:255] or "upload",
        sha256=sha256(content).hexdigest(),
        reader=reader_key(reader),
        kind=reader.source_kind,
    )
    previous = run_for_file(session, source.sha256)
    if previous is not None:
        return ImportPlan(
            source=source, run=None, errors=(RowError(line=0, message=f"already imported as run {previous}"),)
        )
    published = current_policy(session)
    if published is not None and source.kind not in published.policy.source_kinds:
        message = f"the lab's policy doesn't accept {source.kind.name.lower()} imports"
        return ImportPlan(source=source, run=None, errors=(RowError(line=0, message=message),))
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
    ignored = list(parsed.ignored)
    # An instrument measures what its method has, which may be more than this analysis: those
    # analytes are left out and listed, not errors (D-89). A hand-made file has no such excuse.
    instrument = source.kind == SourceKind.INSTRUMENT
    resolved = _resolve(parsed.rows, lookup, errors, ignored if instrument else None)
    calibrations = _calibrations(parsed, lookup)
    run_code = parsed.run.run_code if parsed.run else ""
    fitted = _Fitted()
    _batches(session, resolved, run_code, errors, fitted)
    return ImportPlan(
        source=source,
        run=run,
        new_batches=tuple(fitted.new_batches),
        batch_additions=tuple(fitted.batch_additions),
        readings=tuple(fitted.readings),
        rejected=tuple(fitted.rejected),
        rejected_stored=tuple(fitted.rejected_stored),
        skipped=fitted.skipped,
        calibrations=calibrations,
        ignored=tuple(sorted(set(ignored), key=lambda e: (e.line, e.column, e.message))),
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
    run_id, ids = None, ()
    with session.begin_nested():  # all or nothing, even inside the caller's larger transaction
        for batch in plan.new_batches:
            register_batch(session, batch, importer)
        for addition in plan.batch_additions:
            add_to_batch(session, addition.code, addition.samples, addition.spikes, importer, addition.qc)
        if plan.readings:
            run, ids = record_run(session, plan.run, plan.readings, content, plan.calibrations)
            run_id = run.id
        # Rejected in the run: the technician's call, so recorded as the analyst's exclusion.
        for reading_id, reason in [(ids[i], why) for i, why in plan.rejected] + list(plan.rejected_stored):
            exclude_reading(
                session, ReadingExclusion(reading_id=reading_id, reason=reason, actor=plan.run.analyst)
            )
    return ImportReceipt(
        run_id=run_id,
        readings=len(plan.readings),
        skipped=plan.skipped,
        rejected=len(plan.rejected) + len(plan.rejected_stored),
        batches_registered=tuple(b.code for b in plan.new_batches),
    )


# --- meaning of the rows ----------------------------------------------------------------------------


def _resolve(
    rows, lookup: ReferenceLookup, errors: list[RowError], ignored: list[RowError] | None = None
) -> list[tuple[ParsedRow, Reading]]:
    """Each row as a Reading, checked against the reference data; rows with errors are dropped.
    With `ignored`, an analyte the analysis doesn't have is listed there instead of an error."""
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
                rsd=row.rsd,
                istd_recovery=row.istd_recovery,
                sequence=row.line,  # the file's order is the run's order (D-97)
            )
        except ValidationError as error:
            errors.append(_error(row, "", error.errors()[0]["msg"]))
            continue
        problems = []
        if ignored is not None and not lookup.analyte_valid(analyte):
            message = f"{row.analyte_code} is not an analyte of {row.analysis_code}"
            ignored.append(RowError(line=0, column="analyte", message=message))
            continue
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


def _calibrations(parsed, lookup: ReferenceLookup) -> tuple[Calibration, ...]:
    """The file's calibration, for the analytes of the analysis its rows are for."""
    codes = {row.analysis_code for row in parsed.rows}
    if len(codes) != 1:
        return ()
    analysis_id = lookup.analysis_id(codes.pop())
    if analysis_id is None:
        return ()
    found = []
    for c in parsed.calibrations:
        analyte = AnalyteRef(analysis_id=analysis_id, code=c.analyte_code)
        if lookup.analyte_valid(analyte):
            found.append(Calibration(analyte=analyte, **c.model_dump(exclude={"analyte_code"})))
    return tuple(found)


@dataclass
class _Fitted:
    new_batches: list = field(default_factory=list)
    batch_additions: list = field(default_factory=list)
    readings: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    rejected_stored: list = field(default_factory=list)
    skipped: int = 0


def _batches(session, resolved, run_code: str, errors: list[RowError], out: _Fitted) -> None:
    """Fits the readings to their prep batches: existing ones as stored, new ones registered from the
    file's prep columns (D-24). Compares a re-exported file with what was imported before (D-66): for
    each cup, the file's n-th reading in this run must match the n-th one stored from this run (then
    it is skipped); readings beyond those are new attempts."""
    by_batch = defaultdict(list)
    for row, reading in resolved:
        by_batch[row.prep_batch].append((row, reading))
    for code, items in by_batch.items():
        try:
            existing = get_batch(session, code)
        except NotFound:
            existing = None
        stored = defaultdict(list)  # cup -> [(reading, run_code, excluded)], oldest first
        for reading, *rest in stored_with_runs(session, existing.id) if existing else ():
            # compared with the file as the prep book now says, as the file's own rows are (D-96)
            stored[_key(reading)].append((with_prep_dilution(existing, reading), *rest))
        seen_in_file = defaultdict(int)
        new_items = []
        for row, reading in items:
            reading = _prep_dilution(existing, row, reading, errors)
            if reading is None:
                continue
            key = _key(reading)
            index = seen_in_file[key]
            seen_in_file[key] += 1
            same_run = [entry for entry in stored[key] if entry[1] == run_code]
            reason = row.note or f"rejected in run {run_code}"
            if index < len(same_run):
                before, _, excluded = same_run[index]
                if _content(before) != _content(reading):
                    errors.append(
                        _error(row, "value", f"differs from the reading imported before ({before.value})")
                    )
                elif row.rejected and not excluded:
                    out.rejected_stored.append((before.id, reason))
                out.skipped += 1
                continue
            latest = max((entry[0].attempt for entry in stored[key]), default=0)
            attempt = latest + index - len(same_run) + 1
            new_items.append((row, reading.model_copy(update={"attempt": attempt})))
        if existing is not None:
            _check_existing(existing, new_items, errors, out)
        else:
            batch = _new_batch(code, new_items, errors)
            if batch is not None:
                out.new_batches.append(batch)
        for row, reading in new_items:
            if row.rejected:
                out.rejected.append((len(out.readings), row.note or f"rejected in run {run_code}"))
            out.readings.append((code, reading))


def _prep_dilution(batch: PrepBatch | None, row: ParsedRow, reading: Reading, errors) -> Reading | None:
    """A cup the prep book recorded takes its dilution from there (D-95). The file's factor may be
    left at 1 (not given); any other value must agree, or the row is refused (a sequence mix-up)."""
    if batch is None or reading.sample is None:
        return reading
    prep = batch.prep_dilution(reading.sample, reading.role, reading.replicate)
    if prep is None or reading.dilution == prep:
        return reading
    if reading.dilution != 1:
        errors.append(
            _error(row, "dilution", f"the prep book recorded {prep} for this cup, not {reading.dilution}")
        )
        return None
    return reading.model_copy(update={"dilution": prep})


def _check_existing(batch: PrepBatch, items, errors: list[RowError], out: "_Fitted") -> None:
    """A registered batch keeps its analysis, prep method and recorded spike amounts; a file may add
    samples and spike amounts it doesn't have yet (a run exported while still going, D-66)."""
    samples, spikes, qc = [], {}, {}
    for row, reading in items:
        if reading.analyte.analysis_id != batch.analysis_id:
            errors.append(_error(row, "analysis_code", f"batch {batch.code} is for another analysis"))
        if row.prep_method is not None and row.prep_method != batch.prep_method:
            errors.append(
                _error(row, "prep_method", f"batch {batch.code} was prepped by {batch.prep_method!r}")
            )
        if (
            reading.sample is not None
            and reading.sample not in batch.samples
            and reading.sample not in samples
        ):
            samples.append(reading.sample)
        if row.expected is not None:
            entry = _qc_entry(row, reading, errors)
            stored_qc = batch.qc_expected(reading.role, reading.analyte)
            if entry is None:
                pass
            elif stored_qc is not None and stored_qc != entry:
                errors.append(_error(row, "expected", f"batch {batch.code} records {_qc_text(stored_qc)}"))
            elif stored_qc is None:
                _same_qc(qc, entry, row, errors)
        if row.spike_added is None:
            continue
        amount = Quantity(value=row.spike_added, unit=row.unit)
        stored = batch.spike_added(reading.sample, reading.analyte)
        key = (reading.sample, reading.analyte)
        if stored is not None and stored != amount:
            errors.append(_error(row, "spike_added", f"batch {batch.code} records {stored} for this spike"))
        elif stored is None and spikes.setdefault(key, amount) != amount:
            errors.append(
                _error(row, "spike_added", f"differs from the amount on another row ({spikes[key]})")
            )
    additions = tuple(SpikeAddition(sample=s, analyte=a, added=q) for (s, a), q in spikes.items())
    if samples or additions or qc:
        out.batch_additions.append(
            BatchAddition(code=batch.code, samples=tuple(samples), spikes=additions, qc=tuple(qc.values()))
        )


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
    qc: dict = {}
    for row, reading in items:
        if row.expected is not None:
            entry = _qc_entry(row, reading, errors)
            if entry is not None:
                _same_qc(qc, entry, row, errors)
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
            qc=tuple(qc.values()),
        )
    except ValidationError as error:
        errors.append(_error(first, "prep_batch", f"new batch {code}: {error.errors()[0]['msg']}"))
        return None


def _qc_entry(row: ParsedRow, reading: Reading, errors: list[RowError]) -> QcExpectation | None:
    try:
        return QcExpectation(
            role=reading.role,
            analyte=reading.analyte,
            expected=Quantity(value=row.expected, unit=row.unit),
            material=row.qc_material,
            lot=row.qc_lot,
        )
    except ValidationError as error:
        errors.append(_error(row, "expected", error.errors()[0]["msg"]))
        return None


def _same_qc(seen: dict, entry: QcExpectation, row: ParsedRow, errors: list[RowError]) -> None:
    """Every row of one check standard or CRM states the same expected value, material and lot."""
    first = seen.setdefault((entry.role, entry.analyte), entry)
    if first != entry:
        errors.append(_error(row, "expected", f"differs from another row ({_qc_text(first)})"))


def _qc_text(entry: QcExpectation) -> str:
    return " ".join(str(p) for p in (entry.expected, entry.material, entry.lot) if p is not None)


def _key(reading: Reading) -> tuple:
    return (reading.sample, reading.analyte, reading.role, reading.replicate)


def _content(reading: Reading) -> tuple:
    """What a re-export must repeat exactly, digits included."""
    mdl = None if reading.mdl is None else str(reading.mdl.value)
    evidence = tuple(None if v is None else str(v) for v in (reading.rsd, reading.istd_recovery))
    return (str(reading.value.value), reading.value.unit, mdl, str(reading.dilution), *evidence)


def _error(row: ParsedRow, column: str, message: str) -> RowError:
    return RowError(line=row.line, column=column, message=message)
