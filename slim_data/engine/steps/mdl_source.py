"""mdl_source@1 (D-91): where each reading's MDL comes from, before any other step reads it.

  - "calibration" (the default): each reading keeps the MDL its file gave, worked out with the run's
    calibration (Qtegra's LOD, D-89) or a CSV's column.
  - "specified": the lab's MDL per element and unit, from a study. An element missing from the
    table, or listed in another unit, falls back to the calibration MDL.
Either way a dilution raises the MDL by its factor (effective MDL, D-54). The readings as stored never
change; the step swaps the MDL on the copies the engine works on, and the trace says which source each
result used.
"""

from decimal import Decimal
from typing import Literal, Self

from pydantic import Field, model_validator

from slim_data.domain.base import Dec, Frozen
from slim_data.domain.observations import Reading
from slim_data.domain.values import Quantity
from slim_data.engine.model import BatchState
from slim_data.engine.numbers import text
from slim_data.engine.step import Step


class SpecifiedMdl(Frozen):
    code: str = Field(min_length=1, max_length=20)
    unit: str = Field(min_length=1, max_length=20)
    mdl: Dec = Field(gt=0)


class MdlSourceParams(Frozen):
    mode: Literal["calibration", "specified"] = "calibration"
    mdls: tuple[SpecifiedMdl, ...] = ()

    @model_validator(mode="after")
    def _once_each(self) -> Self:
        keys = [(m.code, m.unit) for m in self.mdls]
        if len(set(keys)) != len(keys):
            raise ValueError("an element and unit is listed twice")
        return self


class MdlSource(Step):
    name = "mdl_source"
    version = 1
    Params = MdlSourceParams
    first = True

    def run(self, state: BatchState, params: MdlSourceParams) -> None:
        table = {(m.code, m.unit): m.mdl for m in params.mdls} if params.mode == "specified" else {}

        def swap(reading: Reading) -> Reading:
            mdl = table.get((reading.analyte.code, reading.value.unit))
            if mdl is None:
                return reading
            return reading.model_copy(update={"mdl": Quantity(value=mdl, unit=reading.value.unit)})

        state.blanks = tuple(swap(r) for r in state.blanks)
        state.qc = tuple(swap(r) for r in state.qc)
        for cell in state.cells.values():
            cell.cups = {replicate: swap(r) for replicate, r in cell.cups.items()}
            cell.spikes = {replicate: swap(r) for replicate, r in cell.spikes.items()}
            used = _mdl_of(cell.cups.values())
            specified = any((r.analyte.code, r.value.unit) in table for r in cell.cups.values())
            self.trace(
                cell,
                mode=params.mode,
                source="specified" if specified else "calibration",
                mdl=None if used is None else text(used),
            )


def _mdl_of(readings) -> Decimal | None:
    mdls = [r.mdl.value for r in readings if r.mdl is not None]
    return max(mdls) if mdls else None
