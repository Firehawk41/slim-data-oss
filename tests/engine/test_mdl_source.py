"""mdl_source@1 (D-91): the run's calibration MDL by default, the lab's specified MDL on a switch."""

import pytest
from pydantic import ValidationError

from slim_data.engine.registry import default_registry
from slim_data.engine.ruleset import RulesetError, StepConfig, validate_ruleset
from slim_data.engine.runner import evaluate
from slim_data.engine.steps.mdl_source import MdlSourceParams
from tests.engine.helpers import DEMO, blank, cups, evaluation_input, step_trace

SPECIFIED = {"mode": "specified", "mdls": [{"code": "Cu", "unit": "ppb", "mdl": "0.20"}]}


def with_mdl_source(params):
    steps = (StepConfig(step="mdl_source@1", params=params), *DEMO.steps)
    return validate_ruleset(DEMO.model_copy(update={"steps": steps}), default_registry())


def run(readings, params, **kw):
    return evaluate(evaluation_input(readings, **kw), with_mdl_source(params)).cells


def test_calibration_is_the_default_each_reading_keeps_its_files_mdl():
    (cell,) = run(cups(1, "Cu", "10.0", "10.2", mdl="0.05"), {})
    assert step_trace(cell, "mdl_source") == {
        "step": "mdl_source@1",
        "mode": "calibration",
        "source": "calibration",
        "mdl": "0.05",
    }
    assert step_trace(cell, "cup_shape")["effective_mdl"] == "0.05"


def test_specified_replaces_the_mdl_and_a_dilution_still_raises_it():
    (cell,) = run(cups(1, "Cu", "10.0", "10.2", mdl="0.05", dilution="10"), SPECIFIED)
    assert step_trace(cell, "mdl_source")["source"] == "specified"
    assert step_trace(cell, "cup_shape")["effective_mdl"] == "2.00"  # 0.20 × 10


def test_an_element_missing_from_the_table_or_in_another_unit_falls_back_to_calibration():
    (zn,) = run(cups(1, "Zn", "10.0", "10.2", mdl="0.05"), SPECIFIED)
    assert step_trace(zn, "mdl_source")["source"] == "calibration"
    assert step_trace(zn, "cup_shape")["effective_mdl"] == "0.05"
    (ppm,) = run(cups(1, "Cu", "10.0", "10.2", mdl="0.05", unit="ppm"), SPECIFIED)
    assert step_trace(ppm, "mdl_source")["source"] == "calibration"


def test_blanks_take_the_specified_mdl_too():
    (cell,) = run([*cups(1, "Cu", "10.0", "10.2"), blank("Cu", "0.010", mdl="0.05")], SPECIFIED)
    assert step_trace(cell, "method_blank")["blank_mdl"] == "0.20"


def test_no_mdl_anywhere_traces_none():
    (cell,) = run(cups(1, "Cu", "10.0", "10.2", mdl=None), {})
    assert step_trace(cell, "mdl_source")["mdl"] is None


def test_it_must_be_the_first_step():
    steps = (DEMO.steps[0], StepConfig(step="mdl_source@1"), *DEMO.steps[1:])
    with pytest.raises(RulesetError, match="must be the first step"):
        validate_ruleset(DEMO.model_copy(update={"steps": steps}), default_registry())


def test_an_element_listed_twice_is_refused():
    entry = {"code": "Cu", "unit": "ppb", "mdl": "0.2"}
    with pytest.raises(ValidationError, match="listed twice"):
        MdlSourceParams(mode="specified", mdls=[entry, entry])
