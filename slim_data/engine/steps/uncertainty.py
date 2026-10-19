"""uncertainty@1 (D-55): ± is half the cup difference, only when the cups agree.

± = |c1 − c2| / 2 on dilution-corrected cups, rounded like the value. N/A (None) when one cup was
chosen (half the gap would describe the rejected cup) and when the result is below MDL. A
replicate-based estimate, not an expanded uncertainty.
"""

from slim_data.domain.enums import Qualifier
from slim_data.engine.model import BatchState, Selection
from slim_data.engine.numbers import text
from slim_data.engine.step import NoParams, Step
from slim_data.engine.steps.reported_value import ReportedValueParams, round_to


class Uncertainty(Step):
    name = "uncertainty"
    version = 1
    requires = ("reported_value",)

    def run(self, state: BatchState, params: NoParams) -> None:
        rv: ReportedValueParams = state.shared["reported_value"]
        for cell in state.cells.values():
            if cell.qualifier is None:
                continue
            if cell.selection != Selection.AGREED:
                self.trace(cell, uncertainty=None, reason="one cup chosen")
            elif cell.qualifier == Qualifier.BELOW_MDL:
                self.trace(cell, uncertainty=None, reason="below MDL")
            else:
                half = abs(cell.cup_value(1) - cell.cup_value(2)) / 2
                cell.uncertainty = round_to(half, rv.places_for(cell.analyte.code))
                self.trace(cell, half_delta=text(half), uncertainty=text(cell.uncertainty))
