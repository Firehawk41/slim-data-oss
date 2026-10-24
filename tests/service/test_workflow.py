"""The workflow service end to end: CSV in → evaluate → review → accept / exception → release →
report issue, including line-by-line imports, frozen released results and corrections (D-67)."""

from decimal import Decimal
from pathlib import Path

import pytest

from slim_data.domain.enums import EventKind, Qualifier, ResultStatus, Role, Verdict
from slim_data.domain.policy import DEMO_POLICY
from slim_data.domain.results import ReportLine
from slim_data.domain.values import Limits
from slim_data.domain.workflow import InvalidTransition
from slim_data.persistence._rows import NotFound
from slim_data.persistence.observations import register_batch
from slim_data.persistence.results import exceptions, issues_for_tr, load_result
from slim_data.persistence.rulesets import publish_policy, publish_ruleset
from slim_data.service import workflow as wf
from slim_data.service.imports import import_run
from slim_data.service.views import batch_view, report_view
from tests.engine.helpers import DEMO
from tests.factories import ANA, ROB, SUE, actor, analyte, sample
from tests.persistence.conftest import make_batch
from tests.service.fakes import FakeLookup, FakeStaff

GOOD = (Path(__file__).parents[1] / "pipeline" / "fixtures" / "good.csv").read_text()
HEADER, *ROWS = GOOD.splitlines()
REV = actor("rev", Role.REVIEWER)
CU = analyte("Cu")
S1, S2 = sample(1), sample(2)
SHA = "f" * 64


def csv(*rows):
    return ("\n".join([HEADER, *rows]) + "\n").encode()


def edit(row, **cells):
    values = row.split(",")
    for column, value in cells.items():
        values[HEADER.split(",").index(column)] = value
    return ",".join(values)


@pytest.fixture
def lab(session):
    publish_ruleset(session, DEMO, SUE)
    publish_policy(session, DEMO_POLICY, SUE)
    lookup = FakeLookup()

    def load(rows=ROWS, name="run42.csv"):
        import_run(session, csv(*rows), name, ROB, lookup, FakeStaff())
        return wf.evaluate_batch(session, "PB-0001", ANA, lookup)

    load.lookup = lookup
    return load


def current(session, s=S1):
    return load_result(session, s, CU)


def test_a_batch_evaluates_into_first_versions_with_who_entered_them(session, lab):
    receipt = lab()
    assert [(str(v.sample), v.version) for v in receipt.new_versions] == [
        ("TR00042-01", 1),
        ("TR00042-02", 1),
    ]
    first = current(session).current
    assert first.reported.value == Decimal("10.100") and first.recovery == Decimal("0.995")
    assert first.analyst_ids == {"ana", "rob"} and first.verdict == Verdict.REPORTABLE
    assert first.trace["selection"] == "AGREED" and first.trace["steps"][0]["step"] == "cup_shape@1"
    second = current(session, S2).current  # one cup has no MDL
    assert second.verdict == Verdict.NEEDS_SUPERVISOR and "MDL_MISSING" in [f.code for f in second.flags]
    again = wf.evaluate_batch(session, "PB-0001", ANA, lab.lookup)
    assert again.new_versions == () and again.unchanged == 2


def test_four_eyes_keeps_the_analyst_and_the_uploader_off_the_review(session, lab):
    lab()
    with pytest.raises(wf.BatchReviewRefused, match="Rob entered readings"):
        wf.review_batch(session, "PB-0001", ROB)
    assert wf.review_batch(session, "PB-0001", REV) == 2
    assert wf.review_batch(session, "PB-0001", REV) == 0  # nothing left in draft


def test_release_sends_the_ready_elements_and_the_rest_wait(session, lab):
    lab(ROWS + [edit(ROWS[0], analyte="Zn", lab_sample_id="TR00042-02", dilution_factor="1")])
    wf.review_batch(session, "PB-0001", REV)
    with pytest.raises(wf.NothingReleased, match="needs an acceptance"):
        wf.release(session, S2, 1, REV)  # Cu: MDL missing; Zn: one cup only
    wf.accept(session, S2, CU, SUE, "MDL taken from the method validation")
    released = wf.release(session, S2, 1, REV)
    assert released.released == ("Cu",) and released.waiting[0][0] == "Zn"
    assert wf.release(session, S1, 1, REV).released == ("Cu",)
    assert current(session).status == ResultStatus.RELEASED


def test_line_by_line_unreleased_results_follow_the_new_readings(session, lab):
    first = lab(ROWS[:2])  # sample 1's cups, no spike or blank yet
    assert first.new_versions[0].version == 1
    early = current(session).current
    assert {"RECOVERY_NOT_MEASURED", "BLANK_MISSING"} <= {f.code for f in early.flags}
    later = lab(ROWS, name="run42-later.csv")
    assert [(str(v.sample), v.version) for v in later.new_versions] == [("TR00042-01", 2), ("TR00042-02", 1)]
    now = current(session)
    assert now.current.verdict == Verdict.REPORTABLE and now.current.reason.startswith("re-evaluated")


def test_released_results_stay_as_reported_and_the_host_is_told_which_to_look_at(session, lab):
    lab()
    wf.review_batch(session, "PB-0001", REV)
    wf.release(session, S1, 1, REV)
    released = current(session).current
    poor_spike = edit(ROWS[2], lab_sample_id="TR00042-02", value="11.2", run_code="RUN-0043")
    receipt = lab([poor_spike], name="run43.csv")  # pooled recovery falls to 0.6475: outside the window
    assert receipt.released_affected == (wf.ResultKey(sample=S1, analyte=CU),)
    assert current(session).current == released  # no new version for the released result
    assert "RECOVERY_OUTSIDE_WINDOW" in [f.code for f in current(session, S2).current.flags]


def test_a_correction_goes_round_again_and_the_report_is_amended(session, lab):
    lab()
    wf.review_batch(session, "PB-0001", REV)
    wf.release(session, S1, 1, REV)
    first = wf.record_issue(session, "TR00042", (ReportLine(sample=S1, analyte=CU, version=1),), SHA, REV)
    assert not first.amended
    wf.start_correction(session, S1, CU, ANA, "wrong dilution entered")
    receipt = wf.evaluate_batch(session, "PB-0001", ANA, lab.lookup)
    assert [(str(v.sample), v.version) for v in receipt.new_versions] == [("TR00042-01", 2)]
    assert current(session).current.reason == "wrong dilution entered"
    wf.review_batch(session, "PB-0001", REV)
    wf.release(session, S1, 1, REV)
    line = (ReportLine(sample=S1, analyte=CU, version=2),)
    with pytest.raises(InvalidTransition, match="must name the issue it replaces"):
        wf.record_issue(session, "TR00042", line, SHA, REV)
    amended = wf.record_issue(session, "TR00042", line, SHA, REV, amends=first.issue_id)
    assert amended.amended
    assert [i.amends for i in issues_for_tr(session, "TR00042")] == [None, first.issue_id]


def test_only_released_versions_go_on_a_report(session, lab):
    lab()
    with pytest.raises(InvalidTransition, match="was not released"):
        wf.record_issue(session, "TR00042", (ReportLine(sample=S1, analyte=CU, version=1),), SHA, REV)


def test_a_rerun_request_holds_the_result_until_new_readings_come(session, lab):
    lab()
    wf.request_rerun(session, S1, CU, REV, "spike looks off")
    assert wf.evaluate_batch(session, "PB-0001", ANA, lab.lookup).new_versions == ()
    assert wf.review_batch(session, "PB-0001", REV) == 1  # sample 2 only
    rerun = edit(ROWS[0], value="10.05", run_code="RUN-0043")
    receipt = lab([rerun], name="run43.csv")
    assert receipt.new_versions[0].version == 2 and not current(session).rerun_requested


def test_releasing_a_rerun_result_needs_a_supervisor_exception(session, lab):
    lab([*ROWS[:2], edit(ROWS[2], value="22")])  # recovery 0.6: outside the window → RERUN
    assert current(session).current.verdict == Verdict.RERUN
    wf.review_batch(session, "PB-0001", REV)
    with pytest.raises(wf.NothingReleased, match="needs a rerun or an exception"):
        wf.release(session, S1, 1, REV)
    event = wf.record_exception(
        session, S1, CU, SUE, "Insufficient sample for a rerun", "no sample left to rerun"
    )
    assert event.kind == EventKind.EXCEPTION
    assert wf.release(session, S1, 1, REV).released == ("Cu",)
    assert current(session).released_with_exception


def test_spec_limits_are_snapshotted_and_nothing_reportable_is_stored_as_such(session, lab):
    lab.lookup.limits[(S1, CU)] = Limits(unit="ppb", high=Decimal("5"))
    lab([*ROWS[:2], edit(ROWS[1], replicate="1", value="14.0", rejected="yes")])
    stored = current(session).current
    assert stored.limits == {"unit": "ppb", "low": None, "high": "5"}
    assert "ABOVE_SPEC" in [f.code for f in stored.flags]
    wf.reject(session, S1, CU, REV, "sample contaminated on receipt")
    assert lab(ROWS, name="all.csv").new_versions[0].sample == S2  # a rejected result isn't re-evaluated
    lab.lookup.limits.clear()
    both = [edit(r, rejected="yes") for r in ROWS[3:5]]
    lab(both, name="none.csv")
    nothing = current(session, S2).current
    assert nothing.qualifier == Qualifier.NOT_REPORTABLE and nothing.reported is None


def test_missing_ruleset_policy_or_result(session):
    register_batch(session, make_batch(), ANA)
    with pytest.raises(NotFound, match="no published ruleset"):
        wf.evaluate_batch(session, "PB-0001", ANA, FakeLookup())
    with pytest.raises(NotFound, match="no policy"):
        wf.accept(session, S1, CU, SUE, "x")
    publish_policy(session, DEMO_POLICY, SUE)
    with pytest.raises(NotFound, match="no result"):
        wf.accept(session, S1, CU, SUE, "x")


def test_a_chemist_alone_signs_off_their_own_results_after_the_delay_labelled_solo(session, lab):
    lab([edit(r, analyst="rob") for r in ROWS])  # Rob runs, uploads and reports alone: a weekend
    with pytest.raises(wf.BatchReviewRefused, match="Rob entered readings"):
        wf.review_batch(session, "PB-0001", ROB)
    with pytest.raises(wf.BatchReviewRefused, match="15 min after the last change"):
        wf.review_batch(session, "PB-0001", ROB, "only chemist on site", solo=True)
    publish_policy(session, DEMO_POLICY.model_copy(update={"name": "no-wait", "solo_review_minutes": 0}), SUE)
    assert wf.review_batch(session, "PB-0001", ROB, "only chemist on site", solo=True) == 2
    assert wf.release(session, S1, 1, ROB, "only chemist on site", solo=True).released == ("Cu",)
    signoffs = exceptions(session, solo=True)
    assert [(str(s), e.kind.name) for s, _, e in signoffs] == [
        ("TR00042-01", "REVIEWED"),
        ("TR00042-02", "REVIEWED"),
        ("TR00042-01", "RELEASED"),
    ]
    assert exceptions(session) == []  # solo sign-offs are not exceptions


@pytest.mark.parametrize(
    ("limits", "shown"),
    [
        ({}, None),  # no spec: ½Δ is never printed
        ({"high": Decimal("10.5")}, None),  # 10.1 is further than ½Δ (0.1) from the limit
        ({"high": Decimal("10.15")}, Decimal("0.100")),  # within ½Δ of the high limit
        ({"high": Decimal("10.0")}, Decimal("0.100")),  # over it
        ({"low": Decimal("10.15")}, Decimal("0.100")),  # under the low limit
        ({"low": Decimal("9.95")}, None),
    ],
)
def test_half_delta_is_printed_only_where_it_bears_on_a_spec_limit(session, lab, limits, shown):
    """D-80: ½Δ goes on the report when the result is outside a limit or within ½Δ of it."""
    if limits:
        lab.lookup.limits[(S1, CU)] = Limits(unit="ppb", **limits)
    lab()
    wf.review_batch(session, "PB-0001", REV)
    wf.release(session, S1, 1, REV)
    line = next(li for li in report_view(session, "TR00042").lines if li.sample == S1)
    assert line.uncertainty == Decimal("0.100") and line.half_delta == shown


def test_the_batch_view_shows_every_cup_and_what_was_chosen(session, lab):
    """D-81: the cups, the choice and the QC numbers, read from each result's trace."""
    lab()
    rows = {r.sample.position: r.cups for r in batch_view(session, "PB-0001").results}
    assert rows[1].cups == ("10.00", "10.20") and rows[1].agree and rows[1].chosen is None
    assert rows[1].half_delta == Decimal("0.100") and rows[1].recovery == Decimal("0.995")
    assert rows[1].mdl == Decimal("0.050") and rows[1].blank == "0.010"
    assert rows[1].recovery_basis == "batch_median"
    assert rows[2].cups == ("5.100", "5.300")  # dilution-corrected (× 10)
