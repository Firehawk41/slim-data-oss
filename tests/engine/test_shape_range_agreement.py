"""cup_shape@1, linear_range@1 and agreement@1 (D-28 to D-30, D-59, D-61)."""

from decimal import Decimal

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from slim_data.domain.enums import ReadingRole
from slim_data.engine.model import Selection
from slim_data.engine.runner import evaluate
from slim_data.engine.steps.agreement import AgreementParams, Zone, replicates_agree
from tests.engine.helpers import codes, cups, demo, evaluation_input, reading, spikes
from tests.factories import analyte, sample

P = AgreementParams()
MDL = Decimal("0.05")


def one(readings, **kw):
    return evaluate(evaluation_input(readings, **kw), demo()).cells[0]


# --- cup_shape --------------------------------------------------------------------------------------


def test_a_cell_needs_two_cups():
    outcome = one([reading(1, "Fe", ReadingRole.CUP, 1, "1.0")])
    assert codes(outcome) == ["CUP_COUNT_UNEXPECTED"] and outcome.selection == Selection.NONE


def test_units_must_match():
    outcome = one([*cups(1, "Fe", "1.0", "1.0"), *spikes(1, "Fe", "11", unit="ppm")])
    assert codes(outcome) == ["UNIT_MISMATCH"]


def test_missing_mdl_falls_back_to_rpd():
    outcome = one(cups(1, "Fe", "0.010", "0.011", mdl=None))
    assert codes(outcome) == ["MDL_MISSING"]
    assert outcome.trace[-1]["zone"] == "rpd"


# --- linear_range -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [("90", []), ("150", ["ABOVE_TOP_STANDARD"]), ("600", ["ABOVE_LINEAR_RANGE"])],
)
def test_linear_range_uses_raw_readings(value, expected):
    outcome = one(cups(1, "Fe", value, value, dilution="10"))
    assert [c for c in codes(outcome) if c.startswith("ABOVE")] == expected


def test_no_range_on_file_means_no_check():
    assert codes(one(cups(1, "Cu", "9999", "9999"))) == []


# --- agreement ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "a, b, agree, zone",
    [
        ("0.01", "0.04", True, Zone.BELOW_MDL),  # both < MDL
        ("0.03", "0.08", True, Zone.ABSOLUTE),  # smaller below 5× MDL: |diff| 0.05 ≤ 1× MDL
        ("0.10", "0.16", False, Zone.ABSOLUTE),  # |diff| 0.06 > 0.05
        ("1.00", "1.20", True, Zone.RPD),  # RPD 18%
        ("1.00", "1.30", False, Zone.RPD),  # RPD 26%
    ],
)
def test_three_zones(a, b, agree, zone):
    assert replicates_agree(Decimal(a), Decimal(b), MDL, P) == (agree, zone)


def test_disagreement_is_flagged_and_dilution_applied():
    outcome = one(cups(1, "Cu", "1.00", "1.30", dilution="10"))
    assert "CUPS_DISAGREE" in codes(outcome)
    assert outcome.trace[1]["cup_1"] == "10.00"


positive = st.decimals(min_value="0", max_value="1000", places=3)


@given(positive, positive)
def test_agreement_is_symmetric(a, b):
    assert replicates_agree(a, b, MDL, P) == replicates_agree(b, a, MDL, P)


@given(positive, positive, st.integers(min_value=-3, max_value=6))
def test_agreement_does_not_depend_on_the_unit(a, b, exponent):
    k = Decimal(10) ** exponent
    assert replicates_agree(a, b, MDL, P) == replicates_agree(a * k, b * k, MDL * k, P)


hundred = st.decimals(min_value="0", max_value="100", places=3)


@given(hundred, st.decimals(min_value="0", max_value="1", places=3), hundred)
def test_no_cliff_a_difference_that_agrees_keeps_agreeing_higher_up(low, diff, rise):
    """D-30: with absolute = RPD × switch, the allowed difference never shrinks as values rise."""
    assume(replicates_agree(low, low + diff, MDL, P)[0])
    assert replicates_agree(low + rise, low + rise + diff, MDL, P)[0]


def test_samples_for_the_property_tests_exist():
    assert sample(1) and analyte("Fe")
