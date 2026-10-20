"""cup_shape@1: checks each cell has the shape the rules expect before anything is calculated.

Two cups (replicates 1 and 2), one unit across cups and spikes, and an MDL on the cups. A cell with
the wrong shape is set aside (nothing reportable) rather than half-evaluated.
"""

from slim_data.engine.model import BatchState, Selection
from slim_data.engine.numbers import text
from slim_data.engine.step import NoParams, Step


class CupShape(Step):
    name = "cup_shape"
    version = 1
    flag_codes = frozenset({"CUP_COUNT_UNEXPECTED", "UNIT_MISMATCH", "MDL_MISSING"})

    def run(self, state: BatchState, params: NoParams) -> None:
        for cell in state.cells.values():
            replicates = sorted(cell.cups)
            if replicates != [1, 2]:
                self.flag(
                    cell, "CUP_COUNT_UNEXPECTED", f"expected cups 1 and 2, found {replicates or 'none'}"
                )
                cell.usable, cell.selection = False, Selection.NONE
                self.trace(cell, usable=False, cups=replicates)
                continue
            units = {r.value.unit for r in [*cell.cups.values(), *cell.spikes.values()]}
            if len(units) != 1:
                self.flag(cell, "UNIT_MISMATCH", f"readings in {sorted(units)}")
                cell.usable, cell.selection = False, Selection.NONE
                self.trace(cell, usable=False, units=sorted(units))
                continue
            cell.unit = units.pop()
            mdls = [r.effective_mdl for r in cell.cups.values()]
            if any(m is None for m in mdls):
                self.flag(cell, "MDL_MISSING", "agreement falls back to the RPD rule alone")
            else:
                cell.mdl = max(m.value for m in mdls)
            self.trace(cell, usable=True, unit=cell.unit, effective_mdl=text(cell.mdl))
