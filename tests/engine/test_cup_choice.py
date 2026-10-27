"""cup_choice@1: the evidence ladder (D-31, D-32, D-35, D-51, D-52)."""

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from slim_data.engine.model import Selection
from slim_data.engine.registry import default_registry
from slim_data.engine.ruleset import StepConfig, validate_ruleset
from slim_data.engine.runner import evaluate
from tests.engine.helpers import DEMO, codes, cups, evaluation_input, spikes, step_trace


def _as_v1(config: StepConfig) -> StepConfig:
    """The demo's cup_choice step as @1: its ladder without the instrument-precision rung."""
    params = {k: v for k, v in config.params.items() if k != "instrument_precision"}
    params["rungs"] = [r for r in params["rungs"] if r != "instrument_precision"]
    return StepConfig(step="cup_choice@1", params=params)


V1 = DEMO.model_copy(
    update={"steps": tuple(_as_v1(c) if c.step.startswith("cup_choice@") else c for c in DEMO.steps)}
)


def demo(**changes):
    """The demo ruleset with cup_choice@1; `changes` replace whole step params by step name."""
    steps = tuple(
        c.model_copy(update={"params": changes[c.step.split("@")[0]]})
        if c.step.split("@")[0] in changes
        else c
        for c in V1.steps
    )
    return validate_ruleset(V1.model_copy(update={"steps": steps}), default_registry())


CUPS_OFF = ("10.0", "14.0")  # RPD 33%: they disagree


def run(readings, ruleset=None, **kw):
    return evaluate(evaluation_input(readings, **kw), ruleset or demo())


def one(readings, **kw):
    return run(readings, **kw).cells[0]


def cup_choice_params(**changes):
    params = dict(next(s for s in V1.steps if s.step == "cup_choice@1").params)
    params.update(changes)
    return demo(cup_choice=params)


# --- rung 1: analyst exclusion -----------------------------------------------------------------------


def test_exclusion_picks_the_other_cup_even_when_cups_agree():
    readings = cups(1, "Cu", "10.0", "10.5")
    outcome = one(readings, excluded=[readings[0].id])
    assert outcome.selection == Selection.CHOSEN and outcome.chosen_replicate == 2
    assert "CUP_CHOSEN_BY_EXCLUSION" in codes(outcome)


def test_excluding_both_cups_leaves_nothing_to_report():
    readings = cups(1, "Cu", *CUPS_OFF)
    outcome = one(readings, excluded=[r.id for r in readings])
    assert outcome.selection == Selection.NONE and "ALL_CUPS_EXCLUDED" in codes(outcome)


# --- spike witness (D-35) ----------------------------------------------------------------------------


def test_spike_witness_picks_the_nearer_cup():
    # added 20; spiked 30.2 → w = 10.2, nearer cup 1 (10.0); margin 3.6 > noise 30.2 × 5% = 1.51
    outcome = one([*cups(1, "Cu", *CUPS_OFF), *spikes(1, "Cu", "30.2")], spike_amounts=[(1, "Cu", "20")])
    assert outcome.chosen_replicate == 1 and "CUP_CHOSEN_BY_SPIKE" in codes(outcome)
    assert step_trace(outcome, "cup_choice")["tried"][0]["witness"] == "conclusive"


def test_two_agreeing_spikes_are_averaged_and_disagreeing_ones_are_ignored():
    agreeing = one(
        [*cups(1, "Cu", *CUPS_OFF), *spikes(1, "Cu", "33.8", "34.2")], spike_amounts=[(1, "Cu", "20")]
    )
    assert agreeing.chosen_replicate == 2
    split = one([*cups(1, "Cu", *CUPS_OFF), *spikes(1, "Cu", "30", "40")], spike_amounts=[(1, "Cu", "20")])
    assert step_trace(split, "cup_choice")["tried"][0]["witness"] == "spikes disagree"


def test_a_noisy_spike_is_inconclusive():
    # added 200; spiked 212 → w = 12, midway between the cups; margin 0 ≤ noise
    outcome = one([*cups(1, "Cu", *CUPS_OFF), *spikes(1, "Cu", "212")], spike_amounts=[(1, "Cu", "200")])
    assert step_trace(outcome, "cup_choice")["tried"][0]["witness"] == "inconclusive"
    assert "CUP_CHOICE_UNDECIDED" in codes(outcome)


def test_witness_needs_a_spike_and_its_amount():
    no_spike = one(cups(1, "Cu", *CUPS_OFF))
    assert step_trace(no_spike, "cup_choice")["tried"][0]["witness"] == "no spike"
    no_amount = one([*cups(1, "Cu", *CUPS_OFF), *spikes(1, "Cu", "30.2")])
    assert step_trace(no_amount, "cup_choice")["tried"][0]["witness"] == "no spike amount recorded"


# --- direction prior (D-52) --------------------------------------------------------------------------


def test_contamination_prone_believes_the_lower_cup_and_loss_prone_the_higher():
    assert one(cups(1, "Zn", *CUPS_OFF)).chosen_replicate == 1
    assert one(cups(1, "Hg", "14.0", "10.0")).chosen_replicate == 1


def test_a_prep_specific_row_overrides_the_element_default():
    assert "CUP_CHOICE_UNDECIDED" in codes(one(cups(1, "As", *CUPS_OFF)))  # NEUTRAL by default
    hot_block = one(cups(1, "As", *CUPS_OFF), prep_method="hot block HNO3")
    assert hot_block.chosen_replicate == 2 and "CUP_CHOSEN_BY_PRIOR" in codes(hot_block)


def test_rung_order_comes_from_the_ruleset():
    readings = [*cups(1, "Zn", *CUPS_OFF), *spikes(1, "Zn", "33.8")]  # spike says cup 2, prior says cup 1
    amounts = [(1, "Zn", "20")]
    assert run(readings, spike_amounts=amounts).cells[0].chosen_replicate == 2
    prior_first = cup_choice_params(rungs=["direction_prior", "spike_witness"])
    assert run(readings, prior_first, spike_amounts=amounts).cells[0].chosen_replicate == 1


def test_undecided_severity_is_set_by_the_ruleset():
    outcome = one(cups(1, "Cu", *CUPS_OFF))
    assert outcome.selection == Selection.NONE
    assert [f.severity.name for f in outcome.flags if f.code == "CUP_CHOICE_UNDECIDED"] == [
        "NEEDS_SUPERVISOR"
    ]


# --- scale fault (D-51) ------------------------------------------------------------------------------

ELEMENTS = ["Cu", "Ni", "Co", "Cr", "Mn", "Pb"]


def scaled_sample(factor="1.30", witness_cups=("Cu", "Ni"), witness_says=2):
    readings, amounts = [], []
    for i, code in enumerate(ELEMENTS):
        base = Decimal(10 + i)
        readings += cups(1, code, str(base * Decimal(factor)), str(base))
        if code in witness_cups:
            true = base * Decimal(factor) if witness_says == 1 else base
            readings += spikes(1, code, str(true + 20))
            amounts.append((1, code, "20"))
    return readings, amounts


def test_a_scaled_cup_is_found_and_decided_once_for_the_sample():
    readings, amounts = scaled_sample()
    evaluation = run(readings, spike_amounts=amounts)
    assert {c.chosen_replicate for c in evaluation.cells} == {2}
    assert all("SCALE_FAULT_CUP_CHOSEN" in codes(c) for c in evaluation.cells)
    assert step_trace(evaluation.cells[0], "cup_choice")["witnesses"] == 2


def test_a_scale_fault_without_enough_witnesses_is_a_rerun():
    readings, amounts = scaled_sample(witness_cups=("Cu",))
    evaluation = run(readings, spike_amounts=amounts)
    assert all("SCALE_FAULT_UNRESOLVED" in codes(c) for c in evaluation.cells)


def test_scale_fault_can_be_switched_off():
    readings, amounts = scaled_sample()
    off = cup_choice_params(scale_fault={"enabled": False})
    assert not any(
        "SCALE_FAULT_CUP_CHOSEN" in codes(c) for c in run(readings, off, spike_amounts=amounts).cells
    )


@settings(max_examples=50, deadline=None)
@given(
    contaminated=st.lists(st.sampled_from(ELEMENTS), min_size=1, max_size=3, unique=True),
    cup=st.sampled_from([1, 2]),
    extra=st.decimals(min_value="3", max_value="50", places=1),
)
def test_scattered_contamination_never_looks_like_a_scale_fault(contaminated, cup, extra):
    readings = []
    for i, code in enumerate(ELEMENTS):
        base = Decimal(10 + i)
        a, b = base, base * Decimal("1.02")
        if code in contaminated:
            a, b = (a + extra, b) if cup == 1 else (a, b + extra)
        readings += cups(1, code, str(a), str(b))
    assert not any(c for cell in run(readings).cells for c in codes(cell) if c.startswith("SCALE_FAULT"))
