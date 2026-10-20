"""The workflow service (DESIGN.md §8, §11): turns a batch's readings into result versions and lets
people act on them. Every function takes the host's session and flushes; the host commits (D-23).

* `evaluate_batch` runs the batch's ruleset and stores a new version only where the outcome changed
  (D-12). Line by line imports call it again and again (D-66): unreleased results follow the latest
  readings; **released results stay as reported** (D-67). If a released result would now come out
  differently, it is listed in `released_affected` so a person can start a correction.
* People's actions wrap the pure rules in `domain/workflow.py` under the published policy.
* `release` releases the elements of a sample + analysis that are ready; the rest wait (D-67).
* `record_issue` stores what a report showed; a report carrying a corrected result must say which
  issue it amends (D-20).
"""

from sqlalchemy.orm import Session

from slim_data.domain import workflow
from slim_data.domain.actor import Actor
from slim_data.domain.base import Frozen
from slim_data.domain.enums import EventKind, Qualifier, ResultStatus
from slim_data.domain.policy import Policy
from slim_data.domain.ports import ReferenceLookup
from slim_data.domain.results import ReportIssue, ReportLine, Result, ResultEvent, ResultVersion
from slim_data.domain.values import AnalyteRef, SampleRef
from slim_data.engine.model import CellOutcome, EvaluationInput, SampleLimits
from slim_data.engine.registry import StepRegistry
from slim_data.engine.runner import evaluate
from slim_data.persistence._rows import NotFound
from slim_data.persistence.observations import entered_by, evaluation_input, get_batch
from slim_data.persistence.results import (
    add_event,
    add_version,
    load_result,
    results_for_batch,
    results_for_tr,
)
from slim_data.persistence.results import (
    record_issue as store_issue,
)
from slim_data.persistence.rulesets import PublishedRuleset, current_policy, ruleset_for


class ResultKey(Frozen):
    sample: SampleRef
    analyte: AnalyteRef

    def __str__(self) -> str:
        return f"{self.sample} {self.analyte.code}"


class EvaluationReceipt(Frozen):
    ruleset_id: int
    new_versions: tuple[ReportLine, ...] = ()
    unchanged: int = 0
    released_affected: tuple[ResultKey, ...] = ()  # released, but would now read differently


class ResultsReleased(Frozen):
    """A fact for the host: these elements of a sample + analysis were released (D-23); the host moves
    the TR to partial or complete report."""

    tr_number: str
    sample: SampleRef
    analysis_id: int
    released: tuple[str, ...]  # analyte codes
    waiting: tuple[tuple[str, str], ...] = ()  # (analyte code, why it isn't released yet)


class ReportIssued(Frozen):
    issue_id: int
    tr_number: str
    amended: bool


class NothingReleased(workflow.WorkflowError):
    def __init__(self, waiting: tuple[tuple[str, str], ...]) -> None:
        super().__init__("; ".join(f"{code}: {why}" for code, why in waiting) or "no results to release")
        self.waiting = waiting


class BatchReviewRefused(workflow.WorkflowError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


# --- evaluation ---------------------------------------------------------------------------------------


def evaluate_batch(
    session: Session,
    code: str,
    actor: Actor,
    lookup: ReferenceLookup,
    reason: str = "re-evaluated after new readings or exclusions",
    registry: StepRegistry | None = None,
) -> EvaluationReceipt:
    batch = get_batch(session, code)
    published = ruleset_for(session, batch.analysis_id, batch.prep_method, registry=registry)
    if published is None:
        raise NotFound(f"no published ruleset for analysis {batch.analysis_id} / {batch.prep_method}")
    stored = evaluation_input(session, code)
    keys = sorted({(r.sample, r.analyte) for r in stored.readings if r.sample is not None}, key=_order)
    limits = [(s, a, lookup.limits_for(s, a)) for s, a in keys]
    data = EvaluationInput(
        batch=stored.batch,
        readings=stored.readings,
        excluded_reading_ids=stored.excluded_reading_ids,
        limits=tuple(
            SampleLimits(sample=s, analyte=a, limits=lim) for s, a, lim in limits if lim is not None
        ),
    )
    evaluation = evaluate(data, published.validated)
    people = entered_by(session, batch.id)
    new_versions, affected, unchanged = [], [], 0
    for cell in evaluation.cells:
        result = load_result(session, cell.sample, cell.analyte)
        candidate = _version(cell, published, people[(cell.sample, cell.analyte)], actor)
        if result is not None and result.status == ResultStatus.REJECTED:
            unchanged += 1
            continue
        if result is not None and result.status == ResultStatus.RELEASED and not result.correction_started:
            if _reads_differently(result.current, candidate):
                affected.append(ResultKey(sample=cell.sample, analyte=cell.analyte))
            unchanged += 1
            continue
        if result is not None and not result.correction_started and _same(result.current, candidate):
            unchanged += 1
            continue
        number = 1 if result is None else result.current.version + 1
        why = "" if number == 1 else _correction_reason(result) or reason
        stored_version = add_version(
            session,
            cell.sample,
            cell.analyte,
            candidate.model_copy(update={"version": number, "reason": why}),
            batch.id,
        )
        new_versions.append(
            ReportLine(sample=cell.sample, analyte=cell.analyte, version=stored_version.version)
        )
    return EvaluationReceipt(
        ruleset_id=published.id,
        new_versions=tuple(new_versions),
        unchanged=unchanged,
        released_affected=tuple(affected),
    )


def _version(cell: CellOutcome, published: PublishedRuleset, analysts: frozenset[str], actor: Actor):
    return ResultVersion(
        version=1,
        ruleset_id=published.id,
        raw=cell.raw,
        reported=cell.reported,
        qualifier=cell.qualifier or Qualifier.NOT_REPORTABLE,
        uncertainty=cell.uncertainty,
        recovery=cell.recovery,
        flags=cell.flags,
        trace={
            "ruleset_hash": published.validated.content_hash,
            "selection": cell.selection.name,
            "chosen_replicate": cell.chosen_replicate,
            "steps": list(cell.trace),
        },
        limits=cell.limits.model_dump(mode="json") if cell.limits else {},
        analyst_ids=analysts,
        created_by=actor,
    )


_COMPARED = (
    "ruleset_id",
    "raw",
    "reported",
    "qualifier",
    "uncertainty",
    "recovery",
    "flags",
    "trace",
    "limits",
)


def _same(current: ResultVersion, candidate: ResultVersion) -> bool:
    if current.analyst_ids != candidate.analyst_ids:
        return False
    return all(getattr(current, f) == getattr(candidate, f) for f in _COMPARED)


def _reads_differently(released: ResultVersion, candidate: ResultVersion) -> bool:
    """What a customer would see change: the number, how it reads, or the verdict behind it. A
    pooled recovery that moved with later spikes alone doesn't count (D-67)."""
    return (released.reported, released.qualifier, released.verdict) != (
        candidate.reported,
        candidate.qualifier,
        candidate.verdict,
    )


def _correction_reason(result: Result) -> str:
    notes = [
        e.note for e in result.events_for(result.current.version) if e.kind == EventKind.CORRECTION_STARTED
    ]
    return notes[-1] if notes else ""


def _order(key):
    return (str(key[0]), key[1].code)


# --- people's actions ----------------------------------------------------------------------------------


def _policy(session: Session) -> Policy:
    published = current_policy(session)
    if published is None:
        raise NotFound("no policy is published")
    return published.policy


def _result(session: Session, sample: SampleRef, analyte: AnalyteRef) -> Result:
    result = load_result(session, sample, analyte)
    if result is None:
        raise NotFound(f"no result for {sample} {analyte.code}")
    return result


def _act(session, sample, analyte, rule, actor, *args) -> ResultEvent:
    policy = _policy(session)
    event = rule(_result(session, sample, analyte), actor, policy, *args)
    return add_event(session, sample, analyte, event)


def accept(session, sample, analyte, actor, reason_category: str, note: str = "") -> ResultEvent:
    return _act(session, sample, analyte, workflow.accept, actor, reason_category, note)


def request_rerun(session, sample, analyte, actor, note: str = "") -> ResultEvent:
    return _act(session, sample, analyte, workflow.request_rerun, actor, note)


def record_exception(session, sample, analyte, actor, reason_category: str, note: str) -> ResultEvent:
    return _act(session, sample, analyte, workflow.record_exception, actor, reason_category, note)


def reject(session, sample, analyte, actor, note: str) -> ResultEvent:
    return _act(session, sample, analyte, workflow.reject, actor, note)


def start_correction(session, sample, analyte, actor, reason: str) -> ResultEvent:
    """Step one of a correction (D-62); the next `evaluate_batch` stores the new version with `reason`."""
    return _act(session, sample, analyte, workflow.start_correction, actor, reason)


def review_batch(session: Session, code: str, actor: Actor, note: str = "") -> int:
    """Reviews every draft of a batch at once (D-19), or none if any can't be: returns how many."""
    policy = _policy(session)
    drafts = [
        r
        for r in results_for_batch(session, get_batch(session, code).id)
        if r.status == ResultStatus.DRAFT and not r.rerun_requested and not r.correction_started
    ]
    events, problems = [], []
    for result in drafts:
        try:
            events.append((result, workflow.review(result, actor, policy, note)))
        except workflow.WorkflowError as error:
            problems.append(f"{result.sample} {result.analyte.code}: {error}")
    if problems:
        raise BatchReviewRefused(problems)
    for result, event in events:
        add_event(session, result.sample, result.analyte, event)
    return len(events)


def release(
    session: Session, sample: SampleRef, analysis_id: int, actor: Actor, note: str = ""
) -> ResultsReleased:
    """Releases the elements of a sample + analysis that are ready; the others wait (D-67)."""
    policy = _policy(session)
    candidates = [
        r
        for r in results_for_tr(session, sample.tr_number)
        if r.sample == sample and r.analyte.analysis_id == analysis_id and r.status != ResultStatus.RELEASED
    ]
    released, waiting = [], []
    for result in candidates:
        try:
            event = workflow.release(result, actor, policy, note)
        except workflow.WorkflowError as error:
            waiting.append((result.analyte.code, str(error)))
            continue
        add_event(session, result.sample, result.analyte, event)
        released.append(result.analyte.code)
    if not released:
        raise NothingReleased(tuple(waiting))
    return ResultsReleased(
        tr_number=sample.tr_number,
        sample=sample,
        analysis_id=analysis_id,
        released=tuple(released),
        waiting=tuple(waiting),
    )


# --- reports -----------------------------------------------------------------------------------------


def record_issue(
    session: Session,
    tr_number: str,
    lines: tuple[ReportLine, ...],
    file_sha256: str,
    actor: Actor,
    amends: int | None = None,
) -> ReportIssued:
    """Records what a report showed. Only released versions may appear; a report carrying a version
    released after a correction is an amended report and must name the issue it replaces (D-20)."""
    for line in lines:
        result = _result(session, line.sample, line.analyte)
        released = [e.version for e in result.events if e.kind == EventKind.RELEASED]
        if line.version not in released:
            raise workflow.InvalidTransition(
                f"{line.sample} {line.analyte.code} v{line.version} was not released"
            )
        if amends is None and min(released) < line.version:
            raise workflow.InvalidTransition(
                f"{line.sample} {line.analyte.code} v{line.version} corrects a reported result: "
                "the report is amended and must name the issue it replaces"
            )
    issue = store_issue(
        session,
        ReportIssue(
            tr_number=tr_number, file_sha256=file_sha256, lines=lines, amends=amends, created_by=actor
        ),
    )
    return ReportIssued(issue_id=issue.id, tr_number=tr_number, amended=issue.is_amended)
