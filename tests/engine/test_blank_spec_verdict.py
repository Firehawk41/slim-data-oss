"""method_blank@1, spec_limits@1 and the verdict (D-56, D-58, D-60, D-64)."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from slim_data.domain.enums import Severity, Verdict
from slim_data.domain.values import Limits
from slim_data.engine.model import EvaluationInput, SampleLimits
from slim_data.engine.registry import default_registry
from slim_data.engine.ruleset import RulesetError, SeverityRule, validate_ruleset
from slim_data.engine.runner import evaluate
from tests.engine.helpers import DEMO, batch, blank, codes, cups, demo, evaluation_input, spikes, step_trace
from tests.factories import analyte, sample


def one(readings, **kw):
    return evaluate(evaluation_input(readings, **kw), demo()).cells[0]


def blank_codes(readings, **kw):
    return codes(one(readings, **kw), "method_blank")


# --- method blank (D-58) -----------------------------------------------------------------------------


def test_a_batch_needs_a_blank_for_the_element_in_its_unit():
    assert blank_codes(cups(1, "Cu", "2", "2")) == ["BLANK_MISSING"]
    assert blank_codes([*cups(1, "Cu", "2", "2"), blank("Cu", "0.01", unit="ppm")]) == ["BLANK_MISSING"]
    assert blank_codes([*cups(1, "Cu", "2", "2"), blank("Zn", "0.01")]) == ["BLANK_MISSING"]


def test_a_blank_at_or_below_mdl_is_fine():
    assert blank_codes([*cups(1, "Cu", "2", "2"), blank("Cu", "0.05")]) == []


def test_the_ten_times_rule_against_the_highest_blank():
    blanks = [blank("Cu", "0.03"), blank("Cu", "0.2", replicate=2)]
    assert blank_codes([*cups(1, "Cu", "2.0", "2.0"), *blanks]) == ["BLANK_DETECTED"]
    assert blank_codes([*cups(1, "Cu", "1.9", "1.9"), *blanks]) == ["BLANK_SIGNIFICANT"]
    assert one([*cups(1, "Cu", "1.9", "1.9"), *blanks]).verdict == Verdict.RERUN


def test_a_result_below_mdl_is_not_affected_by_a_contaminated_blank():
    outcome = one([*cups(1, "Cu", "0.01", "0.02"), blank("Cu", "0.2")])
    assert codes(outcome, "method_blank") == []
    assert step_trace(outcome, "method_blank")["affects"] == "no: result below MDL"


def test_blank_dilution_and_mdl_fallbacks():
    diluted = blank("Cu", "0.02", dilution="10")  # 0.2 effective, MDL 0.5 effective: fine
    assert blank_codes([*cups(1, "Cu", "1", "1"), diluted]) == []
    no_mdl = blank("Cu", "0.2", mdl=None)  # judged against the cups' MDL
    assert blank_codes([*cups(1, "Cu", "1", "1"), no_mdl]) == ["BLANK_SIGNIFICANT"]
    neither = one([*cups(1, "Cu", "1", "1", mdl=None), no_mdl])
    assert step_trace(neither, "method_blank")["contaminated"] is None


def test_an_excluded_blank_is_dropped():
    contaminated = blank("Cu", "0.2")
    assert blank_codes([*cups(1, "Cu", "1", "1"), contaminated], excluded=[contaminated.id]) == [
        "BLANK_MISSING"
    ]


@given(st.decimals(min_value="0", max_value="0.05", places=4), st.decimals("0.01", "100", places=3))
def test_a_blank_at_or_below_mdl_never_raises_a_blank_flag(level, value):
    assert blank_codes([*cups(1, "Cu", str(value), str(value)), blank("Cu", str(level))]) == []


# --- spec limits (D-60) ------------------------------------------------------------------------------


def spec(readings, low=None, high=None, unit="ppb"):
    return one([*readings, blank("Cu", "0.01")], limits=[(1, "Cu", low, high, unit)])


def test_exceedances_are_shown_and_snapshotted_but_never_change_the_verdict():
    above = spec(cups(1, "Cu", "12", "12"), high=Decimal("10"))
    assert codes(above, "spec_limits") == ["ABOVE_SPEC"] and above.limits.high == Decimal("10")
    assert [f.severity for f in above.flags if f.code == "ABOVE_SPEC"] == [Severity.INFO]
    assert codes(spec(cups(1, "Cu", "12", "12"), low=Decimal("15")), "spec_limits") == ["BELOW_SPEC"]
    assert codes(spec(cups(1, "Cu", "12", "12"), low=Decimal("1"), high=Decimal("20")), "spec_limits") == []


def test_limits_in_another_unit_are_not_compared():
    outcome = spec(cups(1, "Cu", "12", "12"), high=Decimal("10"), unit="ppm")
    assert codes(outcome, "spec_limits") == ["SPEC_UNIT_MISMATCH"]


def test_below_mdl_against_spec_limits():
    below = cups(1, "Cu", "0.01", "0.02")  # < 0.05
    assert codes(spec(below, low=Decimal("0.1")), "spec_limits") == ["BELOW_SPEC"]
    assert codes(spec(below, high=Decimal("0.01")), "spec_limits") == ["SPEC_UNDECIDED"]
    assert codes(spec(below, low=Decimal("0.01"), high=Decimal("1")), "spec_limits") == []


def test_nothing_reportable_is_not_compared():
    assert codes(spec(cups(1, "Cu", "10", "14"), high=Decimal("1")), "spec_limits") == []


def test_spec_flags_can_only_be_info():
    ruleset = DEMO.model_copy(
        update={
            "severities": tuple(
                SeverityRule(code="ABOVE_SPEC", severity=Severity.RERUN) if r.code == "ABOVE_SPEC" else r
                for r in DEMO.severities
            )
        }
    )
    with pytest.raises(RulesetError, match="must be INFO"):
        validate_ruleset(ruleset, default_registry())


def test_limits_must_belong_to_the_batch_and_be_unique():
    entry = SampleLimits(sample=sample(2), analyte=analyte("Cu"), limits=Limits(unit="ppb", high=Decimal(1)))
    with pytest.raises(ValidationError, match="not in the batch"):
        EvaluationInput(batch=batch((1,)), readings=tuple(cups(1, "Cu", "1", "1")), limits=(entry,))
    entry = entry.model_copy(update={"sample": sample(1)})
    with pytest.raises(ValidationError, match="two spec limits"):
        EvaluationInput(batch=batch((1,)), readings=tuple(cups(1, "Cu", "1", "1")), limits=(entry, entry))


@given(st.decimals("0.01", "100", places=3), st.decimals("0.01", "100", places=3))
def test_spec_limits_never_change_the_verdict(value, high):
    readings = cups(1, "Cu", str(value), str(value))
    assert spec(readings, high=high).verdict == one([*readings, blank("Cu", "0.01")]).verdict


# --- verdict (D-56) ----------------------------------------------------------------------------------


def test_a_clean_sample_is_reportable_with_no_flags():
    outcome = one(
        [*cups(1, "Cu", "10.0", "10.2"), *spikes(1, "Cu", "30.0"), blank("Cu", "0.01")],
        spike_amounts=[(1, "Cu", "20")],
    )
    assert codes(outcome) == [] and outcome.verdict == Verdict.REPORTABLE
    assert outcome.reported.value == Decimal("10.100") and outcome.uncertainty == Decimal("0.100")


def test_the_worst_flag_decides():
    outcome = one([*cups(1, "Zn", "10.0", "14.0"), blank("Zn", "0.01")])  # WARN prior + NEEDS_SUPERVISOR
    assert {f.severity for f in outcome.flags} == {Severity.INFO, Severity.WARN, Severity.NEEDS_SUPERVISOR}
    assert outcome.verdict == Verdict.NEEDS_SUPERVISOR
