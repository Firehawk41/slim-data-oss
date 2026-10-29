"""A QC-only batch (D-108): blanks and check standards, no samples, so nothing to decide."""

from slim_data.domain.enums import ReadingRole
from slim_data.engine.runner import evaluate
from tests.engine.helpers import demo, evaluation_input, reading


def test_a_qc_only_batch_evaluates_to_no_results():
    readings = [
        reading(None, "Cu", ReadingRole.CHECK, 1, "9.8"),
        reading(None, "Cu", ReadingRole.BLANK, 1, "0.01"),
    ]
    data = evaluation_input(readings, positions=(), expected=((ReadingRole.CHECK, "Cu", "10", "ppb"),))
    assert data.batch.qc_only
    assert evaluate(data, demo()).cells == ()
