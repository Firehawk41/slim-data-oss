"""recovery@1 and recovery_pooling@1 (D-33, D-34, D-36, D-37, D-53)."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from slim_data.domain.enums import Verdict
from slim_data.engine.runner import evaluate
from slim_data.engine.steps.recovery_pooling import PoolingParams
from tests.engine.helpers import blank, codes, cups, demo, evaluation_input, spikes, step_trace

STEPS = ("recovery", "recovery_pooling")


def run(readings, **kw):
    return evaluate(evaluation_input([*readings, blank("Cu", "0.01")], **kw), demo())


def one(readings, **kw):
    return run(readings, **kw).cells[0]


def test_recovery_is_spiked_minus_sample_over_added():
    outcome = one([*cups(1, "Cu", "10.0", "10.0"), *spikes(1, "Cu", "29.8")], spike_amounts=[(1, "Cu", "20")])
    assert outcome.recovery == Decimal("0.99") and codes(outcome) == []
    assert outcome.verdict == Verdict.REPORTABLE
    assert step_trace(outcome, "recovery_pooling")["basis"] == "batch_median"


def test_two_agreeing_spikes_are_averaged():
    outcome = one(
        [*cups(1, "Cu", "10.0", "10.0"), *spikes(1, "Cu", "29.0", "31.0")], spike_amounts=[(1, "Cu", "20")]
    )
    assert outcome.recovery == Decimal(1)


def test_a_sample_over_four_times_the_spike_is_not_evaluable_not_failed():
    outcome = one([*cups(1, "Cu", "100", "100"), *spikes(1, "Cu", "100")], spike_amounts=[(1, "Cu", "20")])
    assert step_trace(outcome, "recovery")["status"] == "not_evaluable"
    assert codes(outcome, *STEPS) == ["SPIKE_NOT_EVALUABLE"] and outcome.recovery is None
    assert outcome.verdict == Verdict.NEEDS_SUPERVISOR


def test_a_pooled_recovery_outside_the_window_reruns():
    outcome = one([*cups(1, "Cu", "10.0", "10.0"), *spikes(1, "Cu", "22")], spike_amounts=[(1, "Cu", "20")])
    assert outcome.recovery == Decimal("0.6") and codes(outcome) == ["RECOVERY_OUTSIDE_WINDOW"]
    assert outcome.verdict == Verdict.RERUN


def test_no_usable_spike_means_recovery_not_measured():
    assert codes(one(cups(1, "Cu", "10", "10")), *STEPS) == ["RECOVERY_NOT_MEASURED"]
    disagree = one(
        [*cups(1, "Cu", "10", "10"), *spikes(1, "Cu", "25", "35")], spike_amounts=[(1, "Cu", "20")]
    )
    assert codes(disagree, *STEPS) == ["SPIKES_DISAGREE", "RECOVERY_NOT_MEASURED"]
    no_amount = one([*cups(1, "Cu", "10", "10"), *spikes(1, "Cu", "30")])
    assert codes(no_amount, *STEPS) == ["SPIKE_AMOUNT_MISSING", "RECOVERY_NOT_MEASURED"]


def test_a_sample_with_nothing_to_report_gives_no_recovery():
    readings = [*cups(1, "Cu", "10", "14"), *spikes(1, "Cu", "30")]
    outcome = one(readings, spike_amounts=[(1, "Cu", "20")], excluded=[r.id for r in readings[:2]])
    assert step_trace(outcome, "recovery")["reason"] == "no sample value"
    assert "RECOVERY_NOT_MEASURED" in codes(outcome)


def batch_of_three(first_cups=("10", "10"), first_spike="28"):
    readings = [
        *cups(1, "Cu", *first_cups),
        *spikes(1, "Cu", first_spike),
        *cups(2, "Cu", "10", "10"),
        *spikes(2, "Cu", "26"),  # 0.8
        *cups(3, "Cu", "10", "10"),
        *spikes(3, "Cu", "20"),  # 0.5: out of window, still counts (D-53)
    ]
    amounts = [(p, "Cu", "20") for p in (1, 2, 3)]
    return run(readings, positions=(1, 2, 3), spike_amounts=amounts)


def test_the_batch_median_of_evaluable_recoveries_is_reported_for_every_sample():
    evaluation = batch_of_three()  # 0.9, 0.8, 0.5
    assert [c.recovery for c in evaluation.cells] == [Decimal("0.8")] * 3
    assert all(codes(c, *STEPS) == [] for c in evaluation.cells)


def test_a_sample_whose_spike_chose_its_cup_uses_the_others_median():
    # sample 1: cups 10/14, spike 30.2 → believes cup 1 (10.0), own recovery 1.01
    evaluation = batch_of_three(first_cups=("10.0", "14.0"), first_spike="30.2")
    first, second, _ = evaluation.cells
    assert step_trace(first, "recovery_pooling")["basis"] == "leave_one_out"
    assert first.recovery == Decimal("0.65")  # median(0.8, 0.5): outside the window
    assert "RECOVERY_OUTSIDE_WINDOW" in codes(first)
    assert second.recovery == Decimal("0.8")  # median(1.01, 0.8, 0.5)


def test_alone_in_the_batch_it_keeps_its_own_recovery_flagged():
    outcome = one([*cups(1, "Cu", "10.0", "14.0"), *spikes(1, "Cu", "30.2")], spike_amounts=[(1, "Cu", "20")])
    assert outcome.recovery == Decimal("1.01")
    assert codes(outcome, *STEPS) == ["RECOVERY_USED_FOR_SELECTION"]


def test_the_window_must_be_ordered():
    with pytest.raises(ValidationError, match="below its high end"):
        PoolingParams(window_low=Decimal("1.2"), window_high=Decimal("0.8"))


amounts = st.decimals(min_value="1", max_value="50", places=2)


@given(
    value=st.decimals(min_value="0", max_value="40", places=3),
    added=amounts,
    r=st.decimals("0.5", "1.5", places=2),
)
def test_recovery_recovers_what_was_put_in(value, added, r):
    spiked = value + r * added
    outcome = one(
        [*cups(1, "Cu", str(value), str(value)), *spikes(1, "Cu", str(spiked))],
        spike_amounts=[(1, "Cu", str(added))],
    )
    if value > 4 * added:
        assert outcome.recovery is None
    else:
        assert outcome.recovery == r


@given(st.lists(st.decimals(min_value="0.5", max_value="1.5", places=2), min_size=1, max_size=6))
def test_the_pooled_recovery_lies_within_the_batch(recoveries):
    readings, positions = [], range(1, len(recoveries) + 1)
    for p, r in zip(positions, recoveries, strict=True):
        readings += [*cups(p, "Cu", "10", "10"), *spikes(p, "Cu", str(10 + 20 * r))]
    evaluation = run(readings, positions=positions, spike_amounts=[(p, "Cu", "20") for p in positions])
    pooled = {c.recovery for c in evaluation.cells}
    assert len(pooled) == 1 and min(recoveries) <= pooled.pop() <= max(recoveries)
