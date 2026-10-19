"""reported_value@1 and uncertainty@1 (D-54, D-55)."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from slim_data.domain.enums import Qualifier
from slim_data.engine.model import Selection
from slim_data.engine.runner import evaluate
from slim_data.engine.steps.reported_value import ReportedValueParams
from tests.engine.helpers import DEMO, cups, demo, evaluation_input, step_trace


def params(**changes):
    p = dict(next(s for s in DEMO.steps if s.step == "reported_value@1").params)
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
