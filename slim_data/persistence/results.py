"""Decided results (DESIGN.md §6.2): identities, immutable versions and the events people add.

A version is never changed: a new one is added, numbered 1..n. The (result, version) primary key is
the optimistic-concurrency check: if two writers both add version 3, the second gets
`ConcurrentChange`. `load_result` reads a result back as of any past moment (D-21).
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from slim_data.domain.actor import Actor
from slim_data.domain.enums import EventKind, Qualifier
from slim_data.domain.results import Flag, ReportIssue, ReportLine, Result, ResultEvent, ResultVersion
from slim_data.domain.values import AnalyteRef, Quantity, SampleRef
from slim_data.persistence._rows import ConcurrentChange, NotFound, actor_json, as_of_filter
from slim_data.persistence.audit import set_change_context
from slim_data.persistence.tables import (
    prep_batches,
    report_issue_lines,
    report_issues,
    result_events,
    result_versions,
    results,
)


def _result_id(session: Session, sample: SampleRef, analyte: AnalyteRef) -> int | None:
    return session.scalar(
        select(results.c.id).where(
            results.c.tr_number == sample.tr_number,
            results.c.position == sample.position,
            results.c.analysis_id == analyte.analysis_id,
            results.c.analyte_code == analyte.code,
        )
    )


def add_version(
    session: Session, sample: SampleRef, analyte: AnalyteRef, version: ResultVersion, batch_id: int
) -> ResultVersion:
    """Adds the next version of a result (creating the result with version 1)."""
    result_id = _result_id(session, sample, analyte)
    latest = 0
    if result_id is not None:
        latest = session.scalar(
            select(result_versions.c.version)
            .where(result_versions.c.result_id == result_id)
            .order_by(result_versions.c.version.desc())
            .limit(1)
        )
    if version.version != latest + 1:
        raise ConcurrentChange(
            f"{sample} {analyte.code}: expected version {latest + 1}, got {version.version}"
        )
    set_change_context(session, version.created_by, version.reason or f"evaluate {sample} {analyte.code}")
    try:
        with session.begin_nested():
            if result_id is None:
                result_id = session.execute(
                    results.insert().values(
                        tr_number=sample.tr_number,
                        position=sample.position,
                        analysis_id=analyte.analysis_id,
                        analyte_code=analyte.code,
                    )
                ).inserted_primary_key[0]
            session.execute(
                result_versions.insert().values(result_id=result_id, batch_id=batch_id, **_row(version))
            )
    except IntegrityError as error:
        raise ConcurrentChange(
            f"{sample} {analyte.code} version {version.version} was written by someone else"
        ) from error
    at = session.scalar(
        select(result_versions.c.created_at).where(
            result_versions.c.result_id == result_id, result_versions.c.version == version.version
        )
    )
    return version.model_copy(update={"created_at": at})


def add_event(session: Session, sample: SampleRef, analyte: AnalyteRef, event: ResultEvent) -> ResultEvent:
    result_id = _result_id(session, sample, analyte)
    if result_id is None:
        raise NotFound(f"no result for {sample} {analyte.code}")
    set_change_context(session, event.actor, event.note or event.kind.name.lower())
    event_id = session.execute(
        result_events.insert().values(
            result_id=result_id,
            version=event.version,
            kind=int(event.kind),
            reason_category=event.reason_category,
            note=event.note,
            solo=event.solo,
            actor_id=event.actor.id,
            actor=actor_json(event.actor),
        )
    ).inserted_primary_key[0]
    at = session.scalar(select(result_events.c.created_at).where(result_events.c.id == event_id))
    return event.model_copy(update={"at": at})


def load_result(
    session: Session, sample: SampleRef, analyte: AnalyteRef, as_of: datetime | None = None
) -> Result | None:
    """The result with every version and event that existed at `as_of`; None if it didn't exist yet."""
    result_id = _result_id(session, sample, analyte)
    return None if result_id is None else _load(session, result_id, sample, analyte, as_of)


def results_for_batch(session: Session, batch_id: int, as_of: datetime | None = None) -> list[Result]:
    """Every result with a version made from this prep batch."""
    ids = session.scalars(
        select(result_versions.c.result_id).where(result_versions.c.batch_id == batch_id).distinct()
    ).all()
    rows = session.execute(
        select(results)
        .where(results.c.id.in_(ids))
        .order_by(results.c.tr_number, results.c.position, results.c.analysis_id, results.c.analyte_code)
    ).all()
    loaded = (_load(session, row.id, *_identity(row), as_of) for row in rows)
    return [result for result in loaded if result is not None]


def every_version(session: Session) -> list[tuple[SampleRef, AnalyteRef, str, ResultVersion]]:
    """Every stored version with its result's identity and the prep batch it was made from, for `verify`."""
    rows = session.execute(
        select(
            results.c.tr_number,
            results.c.position,
            results.c.analysis_id,
            results.c.analyte_code,
            prep_batches.c.code.label("batch_code"),
            result_versions,
        )
        .join(result_versions, result_versions.c.result_id == results.c.id)
        .join(prep_batches, prep_batches.c.id == result_versions.c.batch_id)
        .order_by(
            results.c.tr_number,
            results.c.position,
            results.c.analysis_id,
            results.c.analyte_code,
            result_versions.c.version,
        )
    ).all()
    return [(*_identity(row), row.batch_code, _version(row)) for row in rows]


def record_issue(session: Session, issue: ReportIssue) -> ReportIssue:
    """Stores what a report showed. The caller checks that every line was released (M5 service)."""
    if issue.amends is not None:
        replaced = session.scalar(select(report_issues.c.tr_number).where(report_issues.c.id == issue.amends))
        if replaced is None:
            raise NotFound(f"no report issue {issue.amends} to amend")
        if replaced != issue.tr_number:
            raise ValueError(f"report issue {issue.amends} is for {replaced}, not {issue.tr_number}")
    result_ids = []
    for line in issue.lines:
        result_id = _result_id(session, line.sample, line.analyte)
        if result_id is None:
            raise NotFound(f"no result for {line.sample} {line.analyte.code}")
        result_ids.append(result_id)
    set_change_context(session, issue.created_by, f"report issued for {issue.tr_number}")
    issue_id = session.execute(
        report_issues.insert().values(
            tr_number=issue.tr_number,
            file_sha256=issue.file_sha256,
            amends_id=issue.amends,
            created_by_id=issue.created_by.id,
            created_by=actor_json(issue.created_by),
        )
    ).inserted_primary_key[0]
    session.execute(
        report_issue_lines.insert(),
        [
            {"issue_id": issue_id, "result_id": result_id, "version": line.version}
            for result_id, line in zip(result_ids, issue.lines, strict=True)
        ],
    )
    return issues_for_tr(session, issue.tr_number, only=issue_id)[0]


def issues_for_tr(
    session: Session, tr_number: str, as_of: datetime | None = None, only: int | None = None
) -> list[ReportIssue]:
    """Every report issued for a TR, oldest first (invoicing bills from these, D-43)."""
    condition = (
        report_issues.c.id == only if only is not None else as_of_filter(report_issues.c.created_at, as_of)
    )
    issues = session.execute(
        select(report_issues)
        .where(report_issues.c.tr_number == tr_number, condition)
        .order_by(report_issues.c.created_at, report_issues.c.id)
    ).all()
    found = []
    for issue in issues:
        lines = session.execute(
            select(report_issue_lines.c.version, results)
            .join(results, results.c.id == report_issue_lines.c.result_id)
            .where(report_issue_lines.c.issue_id == issue.id)
            .order_by(report_issue_lines.c.id)
        ).all()
        found.append(
            ReportIssue(
                id=issue.id,
                tr_number=issue.tr_number,
                file_sha256=issue.file_sha256,
                lines=tuple(
                    ReportLine(sample=s, analyte=a, version=line.version)
                    for line in lines
                    for s, a in [_identity(line)]
                ),
                amends=issue.amends_id,
                created_by=Actor.model_validate(issue.created_by),
                created_at=issue.created_at,
            )
        )
    return found


def results_for_tr(session: Session, tr_number: str, as_of: datetime | None = None) -> list[Result]:
    rows = session.execute(
        select(results)
        .where(results.c.tr_number == tr_number)
        .order_by(results.c.position, results.c.analysis_id, results.c.analyte_code)
    ).all()
    loaded = (_load(session, row.id, *_identity(row), as_of) for row in rows)
    return [result for result in loaded if result is not None]


def exceptions(
    session: Session, as_of: datetime | None = None, solo: bool = False
) -> list[tuple[SampleRef, AnalyteRef, ResultEvent]]:
    """Every exception recorded lab-wide (D-09): EXCEPTION events, oldest first. With `solo`, every
    solo sign-off instead (D-68): what an auditor asks for."""
    rows = session.execute(
        select(
            result_events,
            results.c.tr_number,
            results.c.position,
            results.c.analysis_id,
            results.c.analyte_code,
        )
        .join(results, results.c.id == result_events.c.result_id)
        .where(
            result_events.c.solo.is_(True) if solo else result_events.c.kind == int(EventKind.EXCEPTION),
            as_of_filter(result_events.c.created_at, as_of),
        )
        .order_by(result_events.c.created_at, result_events.c.id)
    ).all()
    return [(*_identity(row), _event(row)) for row in rows]


def _load(session, result_id, sample, analyte, as_of) -> Result | None:
    versions = session.execute(
        select(result_versions)
        .where(result_versions.c.result_id == result_id, as_of_filter(result_versions.c.created_at, as_of))
        .order_by(result_versions.c.version)
    ).all()
    if not versions:
        return None
    events = session.execute(
        select(result_events)
        .where(result_events.c.result_id == result_id, as_of_filter(result_events.c.created_at, as_of))
        .order_by(result_events.c.created_at, result_events.c.id)
    ).all()
    return Result(
        id=result_id,
        sample=sample,
        analyte=analyte,
        versions=tuple(_version(row) for row in versions),
        events=tuple(_event(row) for row in events),
    )


def _identity(row) -> tuple[SampleRef, AnalyteRef]:
    return (
        SampleRef(tr_number=row.tr_number, position=row.position),
        AnalyteRef(analysis_id=row.analysis_id, code=row.analyte_code),
    )


def _row(version: ResultVersion) -> dict:
    return {
        "version": version.version,
        "ruleset_id": version.ruleset_id,
        "raw_value": version.raw,
        "reported_value": version.reported.value if version.reported else None,
        "reported_unit": version.reported.unit if version.reported else None,
        "qualifier": int(version.qualifier),
        "uncertainty": version.uncertainty,
        "recovery": version.recovery,
        "flags": [flag.model_dump(mode="json") for flag in version.flags],
        "trace": version.trace,
        "limits": version.limits,
        "analyst_ids": sorted(version.analyst_ids),
        "reason": version.reason,
        "created_by_id": version.created_by.id,
        "created_by": actor_json(version.created_by),
    }


def _version(row) -> ResultVersion:
    return ResultVersion(
        version=row.version,
        ruleset_id=row.ruleset_id,
        raw=row.raw_value,
        reported=None
        if row.reported_value is None
        else Quantity(value=row.reported_value, unit=row.reported_unit),
        qualifier=Qualifier(row.qualifier),
        uncertainty=row.uncertainty,
        recovery=row.recovery,
        flags=tuple(Flag.model_validate(flag) for flag in row.flags),
        trace=row.trace,
        limits=row.limits,
        analyst_ids=frozenset(row.analyst_ids),
        reason=row.reason,
        created_by=Actor.model_validate(row.created_by),
        created_at=row.created_at,
    )


def _event(row) -> ResultEvent:
    return ResultEvent(
        kind=EventKind(row.kind),
        version=row.version,
        actor=Actor.model_validate(row.actor),
        reason_category=row.reason_category,
        note=row.note,
        solo=row.solo,
        at=row.created_at,
    )
