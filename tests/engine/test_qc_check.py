"""qc_check@1 (D-87): check standards and CRMs judged against their expected values."""

import pytest
from pydantic import ValidationError

from slim_data.domain.enums import ReadingRole, Verdict
from slim_data.domain.observations import QcExpectation
from slim_data.engine.runner import evaluate
from slim_data.engine.steps.qc_check import QcCheckParams
from tests.engine.helpers import codes, cups, demo, evaluation_input, qc, step_trace
from tests.factories import analyte, qty

CHECK, CRM = ReadingRole.CHECK, ReadingRole.CRM
WINDOWS = {"check": {"low": "0.90", "high": "1.10"}, "crm": {"low": "0.85", "high": "1.15"}}


def run(readings, expected=((CHECK, "Cu", "10", "ppb"), (CRM, "Cu", "2.0", "ppb")), excluded=(), **params):
    ruleset = demo(qc_check=params or WINDOWS)
    return evaluate(evaluation_input(readings, expected=expected, excluded=excluded), ruleset).cells


def qc_codes(cell):
    return codes(cell, "qc_check")


def test_checks_within_their_windows_pass_and_the_trace_keeps_what_a_chart_plots():
    (cell,) = run([*cups(1, "Cu", "5", "5"), qc(CHECK, "Cu", "10.9"), qc(CRM, "Cu", "1.71")])
    assert qc_codes(cell) == []
    trace = step_trace(cell, "qc_check")
    assert trace["check"] == [
        {
            "replicate": 1,
            "measured": "10.9",
            "expected": "10",
            "recovery": "1.09",
            "within": True,
            "material": None,
            "lot": None,
        }
    ]
    assert trace["crm"][0]["recovery"] == "0.855"


def test_a_failed_check_standard_flags_every_result_of_that_element():
    readings = [*cups(1, "Cu", "5", "5"), *cups(1, "Zn", "5", "5"), qc(CHECK, "Cu", "8.5")]
    cu, zn = run(readings)
    assert qc_codes(cu) == ["CHECK_FAILED"] and qc_codes(zn) == []
    assert cu.flags[-1].message == "check standard recovered 85 %, outside 90 %–110 %"
    assert cu.verdict == Verdict.RERUN


def test_one_failing_replicate_is_enough_and_the_furthest_is_named():
    readings = [
        *cups(1, "Cu", "5", "5"),
        qc(CRM, "Cu", "2.0"),
        qc(CRM, "Cu", "2.4", 2),
        qc(CRM, "Cu", "1.6", 3),
    ]
    (cell,) = run(readings)
    assert qc_codes(cell) == ["CRM_FAILED"]
    assert "recovered 120 %" in cell.flags[-1].message
    assert cell.verdict == Verdict.NEEDS_SUPERVISOR  # a matrix effect can explain a CRM


def test_dilution_counts_and_an_excluded_check_is_dropped():
    diluted = qc(CRM, "Cu", "0.20", dilution="10")  # 2.0 effective: 100 %
    assert qc_codes(run([*cups(1, "Cu", "5", "5"), diluted])[0]) == []
    bad = qc(CHECK, "Cu", "5")
    (cell,) = run([*cups(1, "Cu", "5", "5"), bad], excluded=[bad.id])
    assert qc_codes(cell) == [] and step_trace(cell, "qc_check")["check"] is None


def test_missing_only_when_required_and_unjudged_without_a_window():
    readings = cups(1, "Cu", "5", "5")
    assert qc_codes(run(readings)[0]) == []
    required = {"check": {**WINDOWS["check"], "required": True}, "crm": {**WINDOWS["crm"], "required": True}}
    assert qc_codes(run(readings, **required)[0]) == ["CHECK_MISSING", "CRM_MISSING"]
    (cell,) = run([*readings, qc(CHECK, "Cu", "1")], crm=WINDOWS["crm"])  # no check window: not judged
    assert qc_codes(cell) == [] and "check" not in step_trace(cell, "qc_check")


def test_a_qc_reading_needs_an_expected_value_in_its_unit():
    with pytest.raises(ValidationError, match="no expected value for the CHECK Cu"):
        run([*cups(1, "Cu", "5", "5"), qc(CHECK, "Cu", "10")], expected=())
    with pytest.raises(ValidationError, match="its expected value in ppm"):
        run([*cups(1, "Cu", "5", "5"), qc(CHECK, "Cu", "10")], expected=((CHECK, "Cu", "0.01", "ppm"),))


def test_windows_and_expected_values_are_validated():
    with pytest.raises(ValidationError, match="low end must be below"):
        QcCheckParams.model_validate({"check": {"low": "1.1", "high": "0.9"}})
    with pytest.raises(ValidationError, match="only a CHECK or CRM"):
        QcExpectation(role=ReadingRole.BLANK, analyte=analyte("Cu"), expected=qty("1"))
    with pytest.raises(ValidationError, match="positive"):
        QcExpectation(role=CHECK, analyte=analyte("Cu"), expected=qty("0"))
