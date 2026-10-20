"""Decimal helpers and the step registry (D-17)."""

from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from slim_data.engine.numbers import engine_arithmetic, median, rpd, text
from slim_data.engine.registry import StepRegistry, default_registry
from slim_data.engine.ruleset import RulesetError
from slim_data.engine.steps import ALL_STEPS

MANIFEST = Path(__file__).with_name("step_manifest.txt")


def test_rpd_and_median():
    assert rpd(Decimal(9), Decimal(11)) == Decimal("0.2")
    assert rpd(Decimal(0), Decimal(0)) == 0
    assert median([Decimal(3), Decimal(1), Decimal(2)]) == 2
    assert median([Decimal(1), Decimal(2), Decimal(3), Decimal(10)]) == Decimal("2.5")
    with pytest.raises(ValueError):
        median([])
    assert text(None) is None and text(Decimal("1.50")) == "1.50"


def test_engine_arithmetic_ignores_the_callers_decimal_settings():
    with localcontext() as ctx:
        ctx.prec = 3
        with engine_arithmetic():
            assert Decimal(1) / Decimal(3) == Decimal("0.3333333333333333333333333333")


def test_no_step_version_is_ever_removed_or_added_silently():
    manifest = {line for line in MANIFEST.read_text().splitlines() if line and not line.startswith("#")}
    assert set(default_registry().keys()) == manifest


def test_a_registered_version_cannot_be_replaced():
    registry = StepRegistry()
    registry.register(ALL_STEPS[0]())
    with pytest.raises(ValueError, match="new version"):
        registry.register(ALL_STEPS[0]())
    with pytest.raises(RulesetError, match="unknown step"):
        registry.get("nope@1")


def test_step_contract():
    from slim_data.engine.model import Cell
    from slim_data.engine.step import NoParams, Step
    from tests.factories import analyte, sample

    class Bare(Step):
        name, version = "bare", 1

    with pytest.raises(NotImplementedError):
        Bare().run(None, NoParams())
    with pytest.raises(AssertionError, match="undeclared flag"):
        Bare().flag(Cell(sample=sample(), analyte=analyte()), "SURPRISE")


def test_step_params_are_validated():
    from pydantic import ValidationError

    from slim_data.engine.steps.cup_choice import CupChoiceParams
    from slim_data.engine.steps.linear_range import LinearRangeParams

    fe = {"code": "Fe", "unit": "ppb", "linear_limit": "500"}
    for bad in ({**fe, "linear_limit": "0"}, {**fe, "top_standard": "600"}):
        with pytest.raises(ValidationError):
            LinearRangeParams(ranges=[bad])
    with pytest.raises(ValidationError, match="two linear ranges"):
        LinearRangeParams(ranges=[fe, fe])
    with pytest.raises(ValidationError, match="appears twice"):
        CupChoiceParams(rungs=["spike_witness", "spike_witness"])
    row = {"code": "Zn", "direction": "NEUTRAL"}
    with pytest.raises(ValidationError, match="two direction-prior rows"):
        CupChoiceParams(direction_prior=[row, row])
