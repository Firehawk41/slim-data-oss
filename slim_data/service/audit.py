"""What an auditor reads (D-112): read-only views for the host's vertical audit and for a
nonconformity's impact assessment. Nothing is written.

* `report_audit(issue)`: a report issue and, for each line, the result version it printed with
  everything behind it: the readings the engine saw when it made the version (`as_of`), their runs
  (file, hash, reader, who), the run's calibration, the batch's QC for that analyte, the ruleset, the
  events on that version (who reviewed, accepted, released) and a fresh replay of the engine.
* `issues_between(start, end)`: the reports issued in a period, for picking one to audit.
* `released_in_window(instrument, analyte, start, end)`: every released result version made from a
  batch with sample readings measured on that instrument in that window, with the reports that
  printed it: what a nonconformity may have touched.
"""

from datetime import datetime

from pydantic import AwareDatetime
from sqlalchemy.orm import Session

from slim_data.domain.actor import Actor
from slim_data.domain.base import Dec, Frozen
from slim_data.domain.enums import EventKind, ReadingRole, SourceKind
from slim_data.domain.results import Flag, ReportIssue, ResultEvent
from slim_data.domain.values import AnalyteRef, SampleRef
from slim_data.engine.numbers import engine_arithmetic
from slim_data.engine.registry import StepRegistry
from slim_data.engine.ruleset import content_hash
from slim_data.persistence import observations
from slim_data.persistence import results as stored_results
from slim_data.persistence._rows import NotFound
from slim_data.persistence.rulesets import get_ruleset
from slim_data.service.verify import replay_version
from slim_data.service.views import value_text


class AuditRun(Frozen):
    run_id: int
    run_code: str
    instrument: str
    analyzed_at: AwareDatetime
    file_name: str
    file_sha256: str
    reader: str
    source_kind: SourceKind
    analyst: Actor
    imported_by: Actor
    file_kept: bool  # the file itself is stored (from migration 0006, D-88)


class AuditReading(Frozen):
    reading_id: int
    run_id: int | None
    sample: SampleRef | None
    role: ReadingRole
    replicate: int
    attempt: int
    value: Dec
    unit: str
    dilution: Dec
    mdl: Dec | None = None
    rsd: Dec | None = None
    istd_recovery: Dec | None = None
    conditions: str | None = None
    sequence: int | None = None
    excluded: bool = False
    expected: Dec | None = None  # a CHECK or CRM's
    recovery: Dec | None = None  # measured × dilution ÷ expected


class AuditCalibration(Frozen):
    run_id: int | None
    unit: str
    correlation: Dec | None = None
    determination: Dec | None = None
    lod: Dec | None = None
    bec: Dec | None = None
    conditions: str | None = None


class ResultAudit(Frozen):
    sample: SampleRef
    analyte: AnalyteRef
    version: int
    latest_version: int  # a later version means this one was amended
    value: str  # as the report printed it
    unit: str | None
    verdict: str
    flags: tuple[Flag, ...]
    reason: str
    made_at: AwareDatetime
    made_by: Actor
    analyst_ids: frozenset[str]
    batch_code: str
    prep_method: str
    ruleset_id: int
    ruleset_name: str
    ruleset_hash: str
    readings: tuple[AuditReading, ...]  # this sample's, then the batch's blanks and QC, for this analyte
    runs: tuple[AuditRun, ...]
    calibrations: tuple[AuditCalibration, ...]
    events: tuple[ResultEvent, ...]  # on this version
    replay: str | None  # None: a fresh replay matches what was stored; else what differs
    trace: dict


class ReportAudit(Frozen):
    issue: ReportIssue
    lines: tuple[ResultAudit, ...]


class ReleasedResult(Frozen):
    sample: SampleRef
    analyte: AnalyteRef
    version: int
    latest_version: int
    value: str
    unit: str | None
    released_at: AwareDatetime
    released_by: Actor
    batch_code: str
    runs: tuple[str, ...]  # run codes in the window
    reports: tuple[int, ...]  # report issues that printed this version


def issues_between(
    session: Session, start: datetime | None = None, end: datetime | None = None
) -> list[ReportIssue]:
    return stored_results.issues_between(session, start, end)


def report_audit(session: Session, issue_id: int, registry: StepRegistry | None = None) -> ReportAudit:
    issue = stored_results.get_issue(session, issue_id)
    lines = tuple(
        result_audit(session, line.sample, line.analyte, line.version, registry) for line in issue.lines
    )
    return ReportAudit(issue=issue, lines=lines)


def result_audit(
    session: Session,
    sample: SampleRef,
    analyte: AnalyteRef,
    version: int,
    registry: StepRegistry | None = None,
) -> ResultAudit:
    result = stored_results.load_result(session, sample, analyte)
    if result is None or not 1 <= version <= len(result.versions):
        raise NotFound(f"no version {version} of {sample} {analyte.code}")
    stored = result.versions[version - 1]
    batch_code = stored_results.version_batches(session, sample, analyte)[version]
    then = observations.evaluation_input(session, batch_code, as_of=stored.created_at)
    batch = then.batch
    mine = [
        r
        for r in then.readings
        if r.analyte == analyte and (r.sample == sample or (r.sample is None and r.role.of_batch))
    ]
    mine.sort(key=lambda r: (r.sample is None, int(r.role), r.replicate, r.attempt))
    found = []
    for r in mine:
        entry = batch.qc_expected(r.role, analyte) if r.role.is_qc else None
        recovery = None
        if entry is not None:
            with engine_arithmetic():
                recovery = r.value.value * r.dilution / entry.expected.value
        found.append(
            AuditReading(
                reading_id=r.id,
                run_id=r.run_id,
                sample=r.sample,
                role=r.role,
                replicate=r.replicate,
                attempt=r.attempt,
                value=r.value.value,
                unit=r.value.unit,
                dilution=r.dilution,
                mdl=None if r.mdl is None else r.mdl.value,
                rsd=r.rsd,
                istd_recovery=r.istd_recovery,
                conditions=r.conditions,
                sequence=r.sequence,
                excluded=r.id in then.excluded_reading_ids,
                expected=None if entry is None else entry.expected.value,
                recovery=recovery,
            )
        )
    run_ids = sorted({r.run_id for r in mine if r.run_id is not None})
    runs = []
    for run_id in run_ids:
        run = observations.get_run(session, run_id)
        runs.append(
            AuditRun(
                run_id=run_id,
                run_code=run.run_code,
                instrument=run.instrument,
                analyzed_at=run.analyzed_at,
                file_name=run.source.name,
                file_sha256=run.source.sha256,
                reader=run.source.reader,
                source_kind=run.source.kind,
                analyst=run.analyst,
                imported_by=run.imported_by,
                file_kept=observations.run_file(session, run_id) is not None,
            )
        )
    calibrations = tuple(
        AuditCalibration(
            run_id=c.run_id,
            unit=c.unit,
            correlation=c.correlation,
            determination=c.determination,
            lod=c.lod,
            bec=c.bec,
            conditions=c.conditions,
        )
        for c in then.calibrations
        if c.analyte == analyte and c.run_id in run_ids
    )
    published = get_ruleset(session, id=stored.ruleset_id, registry=registry)
    return ResultAudit(
        sample=sample,
        analyte=analyte,
        version=version,
        latest_version=len(result.versions),
        value=value_text(stored),
        unit=None if stored.reported is None else stored.reported.unit,
        verdict=stored.verdict.name,
        flags=stored.flags,
        reason=stored.reason,
        made_at=stored.created_at,
        made_by=stored.created_by,
        analyst_ids=stored.analyst_ids,
        batch_code=batch_code,
        prep_method=batch.prep_method,
        ruleset_id=published.id,
        ruleset_name=published.ruleset.name,
        ruleset_hash=content_hash(published.ruleset),
        readings=tuple(found),
        runs=tuple(runs),
        calibrations=calibrations,
        events=result.events_for(version),
        replay=replay_version(session, sample, analyte, batch_code, stored, registry),
        trace=stored.trace,
    )


def released_in_window(
    session: Session, instrument: str, analyte_code: str | None, start: datetime, end: datetime
) -> list[ReleasedResult]:
    rows = observations.sample_readings_on(session, instrument, analyte_code, start, end)
    touched: dict[tuple[SampleRef, AnalyteRef], dict[str, set[str]]] = {}
    for row in rows:
        key = (
            SampleRef(tr_number=row.tr_number, position=row.position),
            AnalyteRef(analysis_id=row.analysis_id, code=row.analyte_code),
        )
        touched.setdefault(key, {}).setdefault(row.batch_code, set()).add(row.run_code)
    found = []
    for (sample, analyte), batches in touched.items():
        result = stored_results.load_result(session, sample, analyte)
        if result is None:
            continue
        made_from = stored_results.version_batches(session, sample, analyte)
        printed = stored_results.issues_showing(session, sample, analyte)
        for version in result.versions:
            batch_code = made_from[version.version]
            released = [e for e in result.events_for(version.version) if e.kind == EventKind.RELEASED]
            if batch_code not in batches or not released:
                continue
            found.append(
                ReleasedResult(
                    sample=sample,
                    analyte=analyte,
                    version=version.version,
                    latest_version=len(result.versions),
                    value=value_text(version),
                    unit=None if version.reported is None else version.reported.unit,
                    released_at=released[0].at,
                    released_by=released[0].actor,
                    batch_code=batch_code,
                    runs=tuple(sorted(batches[batch_code])),
                    reports=tuple(printed.get(version.version, ())),
                )
            )
    return sorted(found, key=lambda r: (r.sample.tr_number, r.sample.position, r.analyte.code, r.version))
