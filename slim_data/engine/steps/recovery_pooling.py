"""recovery_pooling@1 (D-33, D-37, D-53): one recovery per element for the batch, judged once.

Per element: the median of the batch's evaluable recoveries (individual out-of-window values still
count). A sample whose cup was chosen by spike witnesses (spike witness or scale fault) gets the
median of the *other* samples (leave-one-out), since its own spike helped pick the value; with no
other sample it keeps its own, flagged. Then:
  - every spike NOT_EVALUABLE → SPIKE_NOT_EVALUABLE (supervisor decides; D-53)
  - no spike for the element in the batch → RECOVERY_NOT_MEASURED
  - the recovery outside the window → RECOVERY_OUTSIDE_WINDOW (the prep or matrix failed; D-53)
Recovery is reported beside the result, never applied to it (D-38).
"""

from decimal import Decimal
from typing import Self

from pydantic import Field, model_validator

from slim_data.domain.base import Dec, Frozen
from slim_data.engine.model import BatchState
from slim_data.engine.numbers import median, text
from slim_data.engine.step import Step
from slim_data.engine.steps.recovery import Status

WITNESS_RUNGS = ("spike_witness", "scale_fault")


class PoolingParams(Frozen):
    window_low: Dec = Field(default=Decimal("0.75"), gt=0)
    window_high: Dec = Field(default=Decimal("1.25"), gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.window_low >= self.window_high:
            raise ValueError("the recovery window's low end must be below its high end")
        return self


class RecoveryPooling(Step):
    name = "recovery_pooling"
    version = 1
    Params = PoolingParams
    requires = ("recovery",)
    flag_codes = frozenset(
        {
            "SPIKE_NOT_EVALUABLE",
            "RECOVERY_NOT_MEASURED",
            "RECOVERY_USED_FOR_SELECTION",
            "RECOVERY_OUTSIDE_WINDOW",
        }
    )

    def run(self, state: BatchState, params: PoolingParams) -> None:
        found = state.shared["recovery"]
        for cell in state.cells.values():
            if not cell.usable:
                continue
            statuses = found.get(cell.analyte.code, [])
            evaluable = [(c.sample, c.own_recovery) for c, s in statuses if s == Status.EVALUABLE]
            if not evaluable:
                if any(s == Status.NOT_EVALUABLE for _, s in statuses):
                    self.flag(
                        cell,
                        "SPIKE_NOT_EVALUABLE",
                        "spike recovery not evaluable: sample concentration exceeds spike level",
                    )
                else:
                    self.flag(cell, "RECOVERY_NOT_MEASURED", "no usable spike for this element in the batch")
                self.trace(cell, recovery=None, basis="none", evaluable=0)
                continue
            basis = "batch_median"
            pool = [r for _, r in evaluable]
            if cell.chosen_by in WITNESS_RUNGS and cell.own_recovery is not None:
                others = [r for s, r in evaluable if s != cell.sample]
                if others:
                    basis, pool = "leave_one_out", others
                else:
                    basis = "own"
                    self.flag(
                        cell,
                        "RECOVERY_USED_FOR_SELECTION",
                        "the only recovery comes from the spike that chose the cup",
                    )
            cell.recovery = median(pool)
            inside = params.window_low <= cell.recovery <= params.window_high
            if not inside:
                self.flag(
                    cell,
                    "RECOVERY_OUTSIDE_WINDOW",
                    f"recovery {cell.recovery} outside {params.window_low}–{params.window_high}",
                )
            self.trace(cell, recovery=text(cell.recovery), basis=basis, evaluable=len(pool), inside=inside)
