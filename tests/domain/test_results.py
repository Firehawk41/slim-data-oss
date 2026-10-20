"""Flags → verdict, result versions, events and the Result aggregate."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from slim_data.domain.enums import EventKind, Qualifier, ResultStatus, Severity, Verdict
from slim_data.domain.results import Flag, ReportIssue, ReportLine, ResultEvent, verdict_from_flags
from tests.factories import ANA, ROB, SUE, analyte, flag, qty, result, sample, version


def event(kind, n=1, actor=ROB, **fields):
    return ResultEvent(kind=kind, version=n, actor=actor, **fields)


@pytest.mark.parametrize(
    "severities, verdict",
    [
        ((), Verdict.REPORTABLE),
        ((Severity.INFO, Severity.WARN), Verdict.REPORTABLE),
        ((Severity.WARN, Severity.NEEDS_SUPERVISOR), Verdict.NEEDS_SUPERVISOR),
        ((Severity.NEEDS_SUPERVISOR, Severity.RERUN, Severity.INFO), Verdict.RERUN),
    ],
)
def test_worst_flag_decides(severities, verdict):
    assert verdict_from_flags(tuple(flag(s) for s in severities)) == verdict


@given(st.lists(st.sampled_from(Severity), max_size=8))
def test_verdict_never_better_than_any_flag(severities):
    verdict = verdict_from_flags(tuple(flag(s) for s in severities))
    rank = {Verdict.REPORTABLE: Severity.WARN, Verdict.NEEDS_SUPERVISOR: Severity.NEEDS_SUPERVISOR,
            Verdict.RERUN: Severity.RERUN}  # fmt: skip
    assert all(s <= rank[verdict] for s in severities)


def test_version_rules():
    assert version(1, flag(Severity.RERUN)).verdict == Verdict.RERUN
    with pytest.raises(ValidationError, match="needs a reason"):
        version(2, reason="  ")
    with pytest.raises(ValidationError, match="below MDL has no"):
        version(qualifier=Qualifier.BELOW_MDL, reported=qty("0.50"))
    with pytest.raises(ValidationError, match="negative"):
        version(uncertainty=Decimal("-0.1"))
    with pytest.raises(ValidationError):
        version(analysts=())
    assert version(qualifier=Qualifier.BELOW_MDL, uncertainty=None).uncertainty is None


@pytest.mark.parametrize(
    "kind, fields, message",
    [
        (EventKind.ACCEPTED, {}, "reason category"),
        (EventKind.EXCEPTION, {"reason_category": "insufficient sample"}, "needs a note"),
        (EventKind.REJECTED, {}, "needs a note"),
        (EventKind.CORRECTION_STARTED, {}, "needs a note"),
    ],
)
def test_events_require_their_fields(kind, fields, message):
    with pytest.raises(ValidationError, match=message):
        event(kind, **fields)


def test_result_shape_is_checked():
    with pytest.raises(ValidationError, match="1..n"):
        result(version(1), version(3))
    with pytest.raises(ValidationError, match="doesn't exist"):
        result(events=(event(EventKind.REVIEWED, n=2),))


def test_status_comes_from_the_current_versions_events():
    r = result()
    assert r.status == ResultStatus.DRAFT
    r = result(events=(event(EventKind.REVIEWED), event(EventKind.RELEASED)))
    assert r.status == ResultStatus.RELEASED
    corrected = result(
        version(1), version(2),
        events=(event(EventKind.REVIEWED), event(EventKind.RELEASED),
                event(EventKind.CORRECTION_STARTED, note="typo in dilution")),
    )  # fmt: skip
    assert corrected.status == ResultStatus.DRAFT
    assert corrected.is_amended
    assert not r.is_amended


def test_clearing_depends_on_the_verdict():
    assert result().is_cleared
    needs = version(1, flag(Severity.NEEDS_SUPERVISOR))
    assert not result(needs).is_cleared
    assert result(needs, events=(event(EventKind.ACCEPTED, actor=SUE, reason_category="matrix"),)).is_cleared
    rerun = version(1, flag(Severity.RERUN))
    assert not result(rerun).is_cleared
    exception = event(
        EventKind.EXCEPTION, actor=SUE, reason_category="no sample left", note="customer agreed"
    )
    assert result(rerun, events=(exception,)).is_cleared


def test_released_with_exception_is_permanent():
    exception = event(
        EventKind.EXCEPTION, actor=SUE, reason_category="no sample left", note="customer agreed"
    )
    released = result(
        version(1, flag(Severity.RERUN)), version(2),
        events=(event(EventKind.REVIEWED), exception, event(EventKind.RELEASED, actor=SUE),
                event(EventKind.CORRECTION_STARTED, actor=ANA, note="new data")),
    )  # fmt: skip
    assert released.released_with_exception
    assert not result(version(1, flag(Severity.RERUN)), events=(exception,)).released_with_exception


def test_flag_needs_code_and_step():
    with pytest.raises(ValidationError):
        Flag(code="", severity=Severity.INFO, step="x@1")


def test_a_report_issue_holds_one_trs_results_once():
    line = ReportLine(sample=sample(1), analyte=analyte(), version=1)
    issue = ReportIssue(tr_number="TR00042", file_sha256="a" * 64, lines=(line,), created_by=ANA)
    assert not issue.is_amended and issue.model_copy(update={"amends": 3}).is_amended
    with pytest.raises(ValidationError, match="every line must be a sample of TR00043"):
        ReportIssue(tr_number="TR00043", file_sha256="a" * 64, lines=(line,), created_by=ANA)
    with pytest.raises(ValidationError, match="appears twice"):
        ReportIssue(tr_number="TR00042", file_sha256="a" * 64, lines=(line, line), created_by=ANA)
