"""End to end (DESIGN.md §12.2): every demo scenario seeded through the service from CSV, then read
back through the report and batch views."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from slim_data.demo.scenarios import ANA, ROB, seed
from slim_data.domain.enums import Qualifier, ResultStatus, Verdict
from slim_data.domain.values import AnalyteRef, SampleRef
from slim_data.persistence._rows import NotFound
from slim_data.persistence.results import exceptions, issues_for_tr
from slim_data.service import workflow as wf
from slim_data.service.views import batch_view, batches, report_view, trace, value_text
from tests.factories import qty, version


@pytest.fixture
def demo(session):
    scenarios, lookup = seed(session, datetime(2026, 10, 20, 9, 0, tzinfo=UTC))
    return scenarios, lookup


def line(view, position, code):
    return next(li for li in view.lines if li.sample.position == position and li.analyte.code == code)


def flags(view, position, code):
    row = next(r for r in view.results if r.sample.position == position and r.analyte.code == code)
    return {f.code for f in row.flags}, row


def test_clean_pass_is_released_and_reported(session, demo):
    view = report_view(session, demo[0]["clean_pass"])
    assert (
        view.complete
        and line(view, 1, "Cu").text == "10.100"
        and line(view, 1, "Cu").uncertainty == Decimal("0.100")
    )
    assert line(view, 1, "Cu").recovery == Decimal("0.99") and line(view, 1, "Cu").notes == ()
    # D-79: the report prints the MDL (and QL), rounded up to the result's places like "<" (D-64)
    assert line(view, 1, "Cu").mdl == Decimal("0.050") and line(view, 1, "Cu").ql == Decimal("0.167")
    assert len(issues_for_tr(session, "TR00101")) == 1


def test_cups_disagree_prior_chooses_and_the_spec_exceedance_is_shown(session, demo):
    reported = line(report_view(session, "TR00102"), 1, "Zn")
    assert (
        reported.text == "10.000" and reported.uncertainty is None and reported.shown_flags == ("ABOVE_SPEC",)
    )
    steps = trace(session, SampleRef(tr_number="TR00102", position=1), AnalyteRef(analysis_id=1, code="Zn"))[
        "steps"
    ]
    assert next(t for t in steps if t["step"] == "cup_choice@1")["rung"] == "direction_prior"
    assert reported.half_delta is None  # one cup believed: no ½Δ (D-55)


def test_a_result_within_half_delta_of_its_spec_prints_the_half_delta(session, demo):
    """D-80: Fe 0.295 is under its 0.3 limit, but ½Δ 0.010 reaches it."""
    fe = line(report_view(session, "TR00102"), 1, "Fe")
    assert fe.text == "0.295" and fe.shown_flags == () and fe.half_delta == Decimal("0.010")


def test_scale_fault_is_decided_for_the_sample_and_waits_for_review(session, demo):
    view = batch_view(session, "PB-00103")
    assert len(view.results) == 6
    for row in view.results:
        codes, _ = flags(view, 1, row.analyte.code)
        assert "SCALE_FAULT_CUP_CHOSEN" in codes and row.status == ResultStatus.DRAFT
        assert row.verdict == Verdict.REPORTABLE
    assert report_view(session, "TR00103").waiting  # not released yet


def test_a_spike_that_chose_its_cup_reports_the_others_recovery(session, demo):
    view = report_view(session, "TR00104")
    assert line(view, 1, "Cu").recovery == Decimal("0.99")  # median of the other two, not its own 1.01
    assert view.complete


def test_below_mdl_reads_less_than(session, demo):
    reported = line(report_view(session, "TR00105"), 1, "F")  # anions (D-78)
    assert reported.text == "< 0.050" and reported.qualifier == Qualifier.BELOW_MDL


def test_rerun_released_by_exception_and_accepted_judgment_call(session, demo):
    exception = line(report_view(session, "TR00106"), 1, "SO4")
    assert exception.released_with_exception and exception.notes == ("Released without rerun",)
    accepted = line(report_view(session, "TR00107"), 1, "Cu")
    assert not accepted.released_with_exception and accepted.notes == (
        "MDL taken from the method validation",
    )
    assert [e.reason_category for _, _, e in exceptions(session)] == ["Released without rerun"]


def test_amended_report_carries_the_corrected_version(session, demo):
    view = report_view(session, "TR00108")
    corrected = line(view, 1, "Assay")  # assay (D-78): the second titration was done again
    assert corrected.version == 2 and corrected.amended and corrected.text == "69.55"
    assert corrected.mdl is None and corrected.ql is None  # a titration has no MDL
    first, second = issues_for_tr(session, "TR00108")
    assert second.amends == first.id
    as_first_reported = report_view(session, "TR00108", as_of=first.created_at)
    assert line(as_first_reported, 1, "Assay").version == 1
    assert line(as_first_reported, 1, "Assay").text == "69.74"  # 69.735, rounded half to even


def test_line_by_line_released_early_and_the_rerun_cup_counts(session, demo):
    view = report_view(session, "TR00109")
    assert [str(li.sample) for li in view.lines] == ["TR00109-01"] and view.waiting == ("TR00109-02 Assay",)
    codes, row = flags(batch_view(session, "PB-00109"), 2, "Assay")
    assert row.text == "68.92" and row.status == ResultStatus.DRAFT  # 68.925: the redone titration counts
    assert not codes & {"CUPS_DISAGREE"}


def test_re_evaluating_the_seeded_demo_changes_nothing(session, demo):
    scenarios, lookup = demo
    for tr in scenarios.values():
        receipt = wf.evaluate_batch(session, f"PB-{tr[2:]}", ANA, lookup)
        assert receipt.new_versions == () and receipt.released_affected == (), tr


def test_views_read_estimates_and_nothing_reportable_and_skip_rejected(session, demo):
    assert (
        value_text(version(1, reported=None, uncertainty=None, qualifier=Qualifier.NOT_REPORTABLE)) == "N/A"
    )
    assert value_text(version(1, reported=qty("0.120"), qualifier=Qualifier.ESTIMATED)) == "0.120 (estimated)"
    scale = SampleRef(tr_number="TR00103", position=1)
    for code in ("Fe", "Cu", "Zn", "Ni", "Cr", "Mn"):
        wf.reject(session, scale, AnalyteRef(analysis_id=2, code=code), ROB, "sample spilled")
    rejected = report_view(session, "TR00103")
    assert rejected.waiting == () and not rejected.complete  # nothing to report, nothing waiting
    with pytest.raises(NotFound):
        trace(session, scale, AnalyteRef(analysis_id=2, code="Fe"), version=9)


def test_batches_lists_every_scenario_batch(session, demo):
    listed = batches(session)
    assert {b.code for b in listed} == {f"PB-{tr[2:]}" for tr in demo[0].values()}
    by_analysis = {(b.analysis_id, b.prep_method) for b in listed}
    assert by_analysis == {(1, "microwave HNO3"), (2, "microwave HNO3"), (3, "dilution"), (4, "as received")}
    assert batches(session, as_of=datetime(2000, 1, 1, tzinfo=UTC)) == ()


def test_the_cups_show_which_cup_was_believed_and_why(session, demo):
    """D-81: a scale fault chose cup 1 for the whole sample; the batch view says so."""
    cups = batch_view(session, "PB-00103").results[0].cups
    assert len(cups.cups) == 2 and not cups.agree and cups.chosen == 1 and cups.why == "scale_fault"
    assert cups.half_delta is None  # one cup chosen: no ½Δ (D-55)
