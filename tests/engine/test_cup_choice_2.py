"""cup_choice@2 (D-88 point 5, D-92): when the cups disagree, the instrument's own evidence can say which
reading was steady. Demo figures: RSD 10 % above 10 × MDL × DF, internal standard 50–150 %."""

from decimal import Decimal

from slim_data.engine.model import Selection
from slim_data.engine.runner import evaluate
from tests.engine.helpers import DEMO, codes, cups, demo, evaluation_input, spikes, step_trace

OFF = ("10.0", "14.0")  # RPD 33 %: the cups disagree


def evidence(readings, rsd=(None, None), istd=(None, None)):
    return [
        r.model_copy(
            update={
                "rsd": None if s is None else Decimal(s),
                "istd_recovery": None if i is None else Decimal(i),
            }
        )
        for r, s, i in zip(readings, rsd, istd, strict=True)
    ]


def one(readings, **kw):
    (cell,) = evaluate(evaluation_input(readings, **kw), demo()).cells
    return cell


def test_a_steady_cup_is_believed_over_one_with_a_high_rsd():
    cell = one(evidence(cups(1, "Cu", *OFF), rsd=("2.0", "18.0")))
    assert cell.selection == Selection.CHOSEN and cell.chosen_replicate == 1
    assert "CUP_CHOSEN_BY_PRECISION" in codes(cell)
    trace = step_trace(cell, "cup_choice")
    assert trace["rung"] == "instrument_precision"
    assert trace["tried"][0]["cup_2"] == {"state": "unstable", "reasons": ["RSD 18.0 %"]}


def test_a_suppressed_internal_standard_makes_a_cup_unstable():
    cell = one(evidence(cups(1, "Cu", *OFF), istd=("40", "96")))
    assert cell.chosen_replicate == 2 and "CUP_CHOSEN_BY_PRECISION" in codes(cell)


def test_both_unstable_or_both_steady_is_inconclusive_and_the_ladder_goes_on():
    for rsd in (("18", "19"), ("2", "3")):
        cell = one(evidence(cups(1, "Cu", *OFF), rsd=rsd))
        assert "CUP_CHOSEN_BY_PRECISION" not in codes(cell)
        assert step_trace(cell, "cup_choice")["tried"][0]["rung"] == "instrument_precision"


def test_no_evidence_like_a_csv_works_as_cup_choice_1_did():
    cell = one(cups(1, "Cu", *OFF))
    tried = step_trace(cell, "cup_choice")["tried"]
    assert tried[0]["cup_1"]["state"] == "unknown" and "CUP_CHOSEN_BY_PRECISION" not in codes(cell)


def test_one_cup_steady_and_the_other_unknown_is_inconclusive():
    cell = one(evidence(cups(1, "Cu", *OFF), rsd=("2.0", None)))
    assert "CUP_CHOSEN_BY_PRECISION" not in codes(cell)


def test_near_the_mdl_the_rsd_does_not_count():
    # 10 × MDL 0.05 = 0.5; cups at 0.30 / 0.45 disagree, but their RSDs aren't evidence
    cell = one(evidence(cups(1, "Cu", "0.30", "0.45"), rsd=("2", "40")))
    assert "CUP_CHOSEN_BY_PRECISION" not in codes(cell)


def test_cups_that_agree_need_no_choice():
    cell = one(evidence(cups(1, "Cu", "10.0", "10.2"), rsd=("2", "40")))
    assert cell.selection == Selection.AGREED


def test_the_rung_order_is_the_rulesets():
    readings = [*evidence(cups(1, "Cu", *OFF), rsd=("18", "2")), *spikes(1, "Cu", "30.1")]
    data = evaluation_input(readings, spike_amounts=[(1, "Cu", "20")])
    params = dict(next(s for s in DEMO.steps if s.step == "cup_choice@2").params)
    params["rungs"] = ["spike_witness", "instrument_precision", "direction_prior"]
    (cell,) = evaluate(data, demo(cup_choice=params)).cells
    assert step_trace(cell, "cup_choice")["rung"] == "spike_witness" and cell.chosen_replicate == 1


def test_without_figures_the_rung_never_decides():
    params = dict(next(s for s in DEMO.steps if s.step == "cup_choice@2").params)
    params["instrument_precision"] = {}
    readings = evidence(cups(1, "Cu", *OFF), rsd=("2", "40"), istd=("10", "100"))
    (cell,) = evaluate(evaluation_input(readings), demo(cup_choice=params)).cells
    assert "CUP_CHOSEN_BY_PRECISION" not in codes(cell)
