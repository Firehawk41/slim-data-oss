"""spec_limits@1 (D-60): over or under the customer's spec is shown, never judged.

Its flags are INFO only (the ruleset is refused otherwise): the verdict says whether the data can be
trusted, not whether the sample passes. Compared on the unrounded value (D-54). A result below MDL
is known only to be under the MDL: below a low limit if the MDL is, and undecided against a high
limit under the MDL. No unit conversion (parked): limits in another unit are not compared.
"""

from slim_data.domain.enums import Qualifier
from slim_data.engine.model import BatchState
from slim_data.engine.numbers import text
from slim_data.engine.step import NoParams, Step

CODES = frozenset({"ABOVE_SPEC", "BELOW_SPEC", "SPEC_UNDECIDED", "SPEC_UNIT_MISMATCH"})


class SpecLimits(Step):
    name = "spec_limits"
    version = 1
    requires = ("reported_value",)
    flag_codes = CODES
    info_only = CODES

    def run(self, state: BatchState, params: NoParams) -> None:
        for (sample, analyte), cell in state.cells.items():
            limits = state.limits.get((sample, analyte))
            value = cell.value()
            if limits is None or value is None:
                continue
            cell.limits = limits
            detail = {"low": text(limits.low), "high": text(limits.high), "unit": limits.unit}
            if limits.unit != cell.unit:
                self.flag(cell, "SPEC_UNIT_MISMATCH", f"limits in {limits.unit}, result in {cell.unit}")
                self.trace(cell, compared=False, **detail)
                continue
            if cell.qualifier == Qualifier.BELOW_MDL:
                if limits.low is not None and cell.mdl <= limits.low:
                    self.flag(cell, "BELOW_SPEC", f"< {cell.mdl}, under the low limit {limits.low}")
                if limits.high is not None and cell.mdl > limits.high:
                    self.flag(cell, "SPEC_UNDECIDED", f"MDL {cell.mdl} is above the high limit {limits.high}")
            elif limits.high is not None and value > limits.high:
                self.flag(cell, "ABOVE_SPEC", f"{value} > high limit {limits.high}")
            elif limits.low is not None and value < limits.low:
                self.flag(cell, "BELOW_SPEC", f"{value} < low limit {limits.low}")
            self.trace(cell, compared=True, value=text(value), **detail)
