"""qc_check@2 (D-97): each element or the average; check standards bracketing the run."""

import pytest
from pydantic import ValidationError

from slim_data.domain.enums import ReadingRole, Verdict
from slim_data.engine.model import EvaluationInput
from slim_data.engine.registry import default_registry
from slim_data.engine.ruleset import StepConfig, validate_ruleset
from slim_data.engine.runner import evaluate
from slim_data.engine.steps.qc_check import QcCheckParams2
from tests.engine.helpers import DEMO, codes, cups, evaluation_input, qc, spikes, step_trace

CHECK, CRM = ReadingRole.CHECK, ReadingRole.CRM
EXPECTED = ((CHECK, "Cu", "10", "ppb"), (CHECK, "Zn", "10", "ppb"), (CRM, "Cu", "2.0", "ppb"))


def ruleset(crm=None, **check):
    params = {"check": {"low": "0.75", "high": "1.25", **check}, "crm": crm}
    steps = tuple(
        StepConfig(step="qc_check@2", params=params) if c.step.startswith("qc_check@") else c
        for c in DEMO.steps
    )
    return validate_ruleset(DEMO.model_copy(update={"steps": steps}), default_registry())


def at(readings, *sequences, run_id=1):
    """The readings with their line in run `run_id` (D-97)."""
    return [
        r.model_copy(update={"sequence": s, "run_id": run_id})
        for r, s in zip(readings, sequences, strict=True)
    ]


def run(readings, codes_by_run=((1, "RUN-1"),), crm=None, **check):
    data = evaluation_input(readings, expected=EXPECTED)
    data = EvaluationInput(**{**dict(data), "run_codes": codes_by_run})
    return evaluate(data, ruleset(crm, **check)).cells


def qc_codes(cell):
    return codes(cell, "qc_check")


def test_without_run_order_it_judges_batch_wide_as_version_1():
    (cell,) = run([*cups(1, "Cu", "5", "5"), qc(CHECK, "Cu", "7.0")], bracket=True)
    assert qc_codes(cell) == ["CHECK_FAILED"] and "line" not in cell.flags[-1].message


def test_a_failed_check_flags_only_the_samples_it_brackets():
    first = at([qc(CHECK, "Cu", "10.1"), *cups(1, "Cu", "5", "5"), qc(CHECK, "Cu", "7.0", 2)], 2, 3, 4, 5)
    second = at([*cups(2, "Cu", "5", "5"), qc(CHECK, "Cu", "9.9", 3)], 6, 7, 8)
    late = at(
        [qc(CHECK, "Cu", "10.0", 4), *cups(3, "Cu", "5", "5"), qc(CHECK, "Cu", "10.2", 5)], 9, 10, 11, 12
    )
    data = evaluation_input([*first, *second, *late], positions=(1, 2, 3), expected=EXPECTED)
    data = EvaluationInput(**{**dict(data), "run_codes": ((1, "RUN-1"),)})
    one, two, three = evaluate(data, ruleset(bracket=True)).cells
    assert qc_codes(one) == ["CHECK_FAILED"] and "line 5 of run RUN-1" in one.flags[-1].message
    assert qc_codes(two) == ["CHECK_FAILED"]  # its check before (line 5) failed
    assert qc_codes(three) == []  # bracketed by lines 9 and 12, both passed
    assert step_trace(three, "qc_check")["check_brackets"][0] == {
        "reading": "CUP 1", "run": "RUN-1", "sequence": 10, "before": 9, "after": 12,
    }  # fmt: skip


def test_a_sample_with_no_check_after_it_is_not_bracketed():
    readings = at([qc(CHECK, "Cu", "10.0"), *cups(1, "Cu", "5", "5"), *spikes(1, "Cu", "6")], 2, 3, 4, 5)
    (cell,) = run(readings, bracket=True)
    assert "CHECK_NOT_BRACKETED" in qc_codes(cell)
    assert cell.flags[-1].message == "no check standard before and after cup 1, cup 2, spike 1 in its run"


def test_a_run_re_exported_as_two_imports_is_one_run():
    first = at([qc(CHECK, "Cu", "10.0"), *cups(1, "Cu", "5", "5")], 2, 3, 4, run_id=1)
    later = at([qc(CHECK, "Cu", "10.0", 2)], 5, run_id=2)
    (cell,) = run([*first, *later], codes_by_run=((1, "RUN-1"), (2, "RUN-1")), bracket=True)
    assert qc_codes(cell) == []


def test_judging_the_average_lets_one_element_stray():
    readings = [*cups(1, "Cu", "5", "5"), qc(CHECK, "Cu", "7.0"), qc(CHECK, "Zn", "12.0")]  # 70 % and 120 %
    (each,) = run(readings)
    (average,) = run(readings, judge="average")  # (0.70 + 1.20) / 2 = 95 %
    assert qc_codes(each) == ["CHECK_FAILED"] and qc_codes(average) == []
    assert step_trace(average, "qc_check")["check"][0]["elements"] == {"Cu": "0.7", "Zn": "1.2"}


def test_an_average_check_that_fails_flags_every_element_and_says_so():
    readings = [*cups(1, "Zn", "5", "5"), qc(CHECK, "Cu", "6.0"), qc(CHECK, "Zn", "7.0")]
    (cell,) = run(at(readings, 3, 4, 2, 2), judge="average", bracket=False)
    assert qc_codes(cell) == ["CHECK_FAILED"] and "averaged over its elements, 65 %" in cell.flags[-1].message
    assert cell.verdict == Verdict.RERUN


def test_required_and_missing_and_the_crm_never_brackets():
    (cell,) = run(cups(1, "Cu", "5", "5"), required=True)
    assert qc_codes(cell) == ["CHECK_MISSING"]
    with pytest.raises(ValidationError, match="doesn't bracket"):
        QcCheckParams2(crm={"low": "0.8", "high": "1.2", "bracket": True})


def test_each_role_judges_its_own_readings_and_an_optional_one_may_be_absent():
    (cell,) = run([*cups(1, "Cu", "5", "5"), qc(CHECK, "Cu", "10")], crm={"low": "0.8", "high": "1.2"})
    assert qc_codes(cell) == [] and step_trace(cell, "qc_check")["crm"] is None
