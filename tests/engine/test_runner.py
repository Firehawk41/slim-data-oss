"""The runner: input checks, the batch size limit (D-07) and replay (D-17)."""

import pytest
from pydantic import ValidationError

from slim_data.domain.enums import ReadingRole
from slim_data.engine.model import EvaluationInput
from slim_data.engine.runner import BatchRejected, evaluate, replay
from tests.engine.helpers import batch, blank, cups, demo, evaluation_input, reading, spikes


def test_replay_reproduces_the_stored_evaluation():
    readings = [*cups(1, "Cu", "10.0", "14.0"), *spikes(1, "Cu", "30.2"), *cups(1, "Zn", "1", "1")]
    data = evaluation_input(readings, spike_amounts=[(1, "Cu", "20")])
    stored = evaluate(data, demo())
    assert replay(data, demo(), stored) == []
    assert stored.cell(data.readings[0].sample, data.readings[0].analyte).chosen_replicate == 1


def test_replay_reports_what_changed():
    data = evaluation_input(cups(1, "Cu", "10.0", "14.0"))
    stored = evaluate(data, demo())
    stricter = demo(agreement={"switch_multiple": "5", "rpd_limit": "0.50", "absolute_multiple": "2.5"})
    differences = replay(data, stricter, stored)
    assert "ruleset content hash differs" in differences[0]
    assert "trace or flags differ" in differences[1]
    extra = evaluation_input([*cups(1, "Cu", "10.0", "14.0"), *cups(1, "Zn", "1", "1")])
    assert "present in only one" in replay(extra, demo(), stored)[0]
    with pytest.raises(KeyError):
        stored.cell(extra.readings[2].sample, extra.readings[2].analyte)


def test_the_batch_size_limit_is_enforced():
    with pytest.raises(BatchRejected, match="allows 20"):
        evaluate(evaluation_input(cups(1, "Cu", "1", "1"), positions=range(1, 22)), demo())


def test_input_is_checked():
    with pytest.raises(ValidationError, match="not in batch"):
        EvaluationInput(batch=batch((1,)), readings=tuple(cups(2, "Cu", "1", "1")))
    with pytest.raises(ValidationError, match="not this batch's analysis"):
        other = reading(1, "Cu", ReadingRole.CUP, 1, "1")
        other = other.model_copy(update={"analyte": other.analyte.model_copy(update={"analysis_id": 2})})
        EvaluationInput(batch=batch((1,)), readings=(other,))
    with pytest.raises(ValidationError, match="not in this input"):
        evaluation_input(cups(1, "Cu", "1", "1"), excluded=[999_999])
    with pytest.raises(ValueError, match="two CUP readings"):
        evaluate(evaluation_input([*cups(1, "Cu", "1", "1"), *cups(1, "Cu", "1", "1")]), demo())
    sp = spikes(1, "Cu", "5")
    with pytest.raises(ValueError, match="only cup readings"):
        evaluate(evaluation_input([*cups(1, "Cu", "1", "1"), *sp], excluded=[sp[0].id]), demo())


def test_blanks_are_kept_aside_for_the_blank_step():
    data = evaluation_input([*cups(1, "Cu", "1", "1"), blank("Cu", "0.01")])
    assert len(evaluate(data, demo()).cells) == 1
