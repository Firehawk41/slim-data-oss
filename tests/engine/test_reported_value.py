"""reported_value@1 / @2 and uncertainty@1 (D-54, D-55, D-94)."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from slim_data.domain.enums import Qualifier
from slim_data.engine.model import Selection
from slim_data.engine.registry import default_registry
from slim_data.engine.ruleset import StepConfig, validate_ruleset
from slim_data.engine.runner import evaluate
from slim_data.engine.steps.reported_value import ReportedValueParams
from tests.engine.helpers import DEMO, cups, demo, evaluation_input, step_trace


def params(**changes):
    p = dict(next(s for s in DEMO.steps if s.step.startswith("reported_value@")).params)
    p.update(changes)
    return demo(reported_value=p)


def one(readings, ruleset=None, **kw):
    return evaluate(evaluation_input(readings, **kw), ruleset or demo()).cells[0]


def test_agreeing_cups_report_their_mean_rounded_once_with_half_the_difference():
    outcome = one(cups(1, "Cu", "12.40", "12.60"))
    assert outcome.raw == Decimal("12.50") and outcome.reported.value == Decimal("12.500")
    assert outcome.reported.unit == "ppb" and outcome.qualifier == Qualifier.DETECTED
    assert outcome.uncertainty == Decimal("0.100")


def test_dilution_applies_to_value_and_plus_minus():
    outcome = one(cups(1, "Cu", "1.240", "1.260", dilution="10"))
    assert outcome.reported.value == Decimal("12.500") and outcome.uncertainty == Decimal("0.100")


def test_rounding_is_half_to_even():
    assert one(cups(1, "Cu", "1.0005", "1.0005")).reported.value == Decimal("1.000")
    assert one(cups(1, "Cu", "1.0015", "1.0015")).reported.value == Decimal("1.002")


def test_below_the_effective_mdl_reads_less_than_the_mdl_rounded_up():
    outcome = one(cups(1, "Cu", "0.004", "0.006", mdl="0.01234"))
    assert outcome.qualifier == Qualifier.BELOW_MDL and outcome.reported.value == Decimal("0.013")
    assert outcome.uncertainty is None
    assert step_trace(outcome, "uncertainty")["reason"] == "below MDL"


def test_between_mdl_and_ql_is_estimated_unless_the_ruleset_has_no_ql():
    assert one(cups(1, "Cu", "0.10", "0.10")).qualifier == Qualifier.ESTIMATED  # QL = 3.33 × 0.05
    assert one(cups(1, "Cu", "0.10", "0.10"), params(ql_multiple=None)).qualifier == Qualifier.DETECTED
    assert one(cups(1, "Cu", "0.10", "0.10", mdl=None)).qualifier == Qualifier.DETECTED


def test_decimal_places_can_be_set_per_analyte():
    ruleset = params(places=[{"code": "Cu", "places": 1}])
    outcome = one(cups(1, "Cu", "12.40", "12.62"), ruleset)
    assert outcome.reported.value == Decimal("12.5") and outcome.uncertainty == Decimal("0.1")


def test_a_chosen_cup_is_reported_alone_without_plus_minus():
    outcome = one(cups(1, "Zn", "10.0", "14.0"))  # Zn is contamination-prone: the lower cup
    assert outcome.selection == Selection.CHOSEN and outcome.reported.value == Decimal("10.000")
    assert outcome.uncertainty is None
    assert step_trace(outcome, "uncertainty")["reason"] == "one cup chosen"


def test_nothing_reportable_reports_nothing():
    outcome = one(cups(1, "Cu", "10.0", "14.0"))  # Cu has no prior and no spike: undecided
    assert outcome.reported is None and outcome.qualifier is None and outcome.raw is None


def test_places_are_unique():
    with pytest.raises(ValidationError, match="two decimal-place rows"):
        ReportedValueParams(places=[{"code": "Cu", "places": 1}, {"code": "Cu", "places": 2}])


values = st.decimals(min_value="0.2", max_value="300", places=5)


@given(a=values, b=values)
def test_reported_is_within_half_a_last_place_and_plus_minus_is_symmetric(a, b):
    first = one(cups(1, "Cu", str(a), str(b)))
    swapped = one(cups(1, "Cu", str(b), str(a)))
    assert first.uncertainty == swapped.uncertainty
    if first.raw is not None:
        assert abs(first.reported.value - first.raw) <= Decimal("0.0005")
        assert first.uncertainty is None or first.uncertainty >= 0


# D-94: reported_value@2 compares against the MDL of the cups the result kept.
V1 = validate_ruleset(
    DEMO.model_copy(
        update={
            "steps": tuple(
                StepConfig(step="reported_value@1", params=c.params)
                if c.step.startswith("reported_value@")
                else c
                for c in DEMO.steps
            )
        }
    ),
    default_registry(),
)


def mixed(v1, v2, **kw):
    """Cup 1 diluted 10×, cup 2 20×: effective MDLs 0.5 and 1.0 (MDL 0.05)."""
    a, _ = cups(1, "Cu", v1, v1, dilution="10", **kw)
    _, b = cups(1, "Cu", v2, v2, dilution="20", **kw)
    return [a, b]


def test_agreeing_cups_use_the_mdl_times_their_average_dilution():
    outcome = one(mixed("1.00", "0.505"))
    assert outcome.selection == Selection.AGREED
    trace = step_trace(outcome, "reported_value")
    assert trace["mdl_from"] == "both cups" and Decimal(trace["effective_mdl"]) == Decimal("0.75")


def test_a_value_between_the_average_and_the_larger_mdl_is_reported_by_2_not_by_1():
    readings = mixed("0.08", "0.04")  # 0.8 effective
    assert one(readings, V1).qualifier == Qualifier.BELOW_MDL
    outcome = one(readings)
    assert outcome.qualifier == Qualifier.ESTIMATED and outcome.reported.value == Decimal("0.800")


@pytest.mark.parametrize("excluded, mdl", [(0, "1.0"), (1, "0.5")])
def test_a_chosen_cup_uses_its_own_dilution(excluded, mdl):
    readings = mixed("1.00", "0.505")
    outcome = one(readings, excluded=[readings[excluded].id])
    assert outcome.selection == Selection.CHOSEN
    trace = step_trace(outcome, "reported_value")
    assert trace["mdl_from"] == f"cup {2 - excluded}" and Decimal(trace["effective_mdl"]) == Decimal(mdl)


def test_without_an_mdl_there_is_nothing_to_keep():
    trace = step_trace(one(cups(1, "Cu", "10.0", "10.1", mdl=None)), "reported_value")
    assert trace["mdl_from"] is None and trace["effective_mdl"] is None


def test_version_1_keeps_the_larger_mdl():
    trace = step_trace(one(mixed("1.00", "0.505"), V1), "reported_value")
    assert "mdl_from" not in trace and Decimal(trace["effective_mdl"]) == Decimal("1.0")


def test_an_evaporation_concentrates_value_and_mdl():  # dilution below 1 (D-93)
    outcome = one(cups(1, "Cu", "1.00", "1.00", dilution="0.1"))
    assert outcome.reported.value == Decimal("0.100")
    assert Decimal(step_trace(outcome, "reported_value")["effective_mdl"]) == Decimal("0.005")
