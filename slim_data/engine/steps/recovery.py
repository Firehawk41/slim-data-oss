"""recovery@1 (D-34, D-36): each sample's own spike recovery = (spiked − sample value) / spike added.

Two spikes are checked against each other with the cups' agreement test (D-34) and averaged; spikes
that disagree give no recovery. A sample above 4× the spike added is NOT_EVALUABLE, not failed: the
spike is too small to see against it. Individual recoveries are not judged here; recovery_pooling
judges the batch (D-37, D-53).
"""

from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from slim_data.domain.base import Dec, Frozen
from slim_data.engine.model import BatchState, Cell
from slim_data.engine.numbers import text
from slim_data.engine.step import Step
from slim_data.engine.steps.agreement import AgreementParams, replicates_agree


class RecoveryParams(Frozen):
    not_evaluable_multiple: Dec = Field(default=Decimal(4), gt=0)  # the 4× rule


class Status(StrEnum):
    EVALUABLE = "evaluable"
    NOT_EVALUABLE = "not_evaluable"  # sample > 4× spike added
    UNUSABLE = "unusable"  # no sample value, spikes disagree, or no amount: says nothing either way


class Recovery(Step):
    name = "recovery"
    version = 1
    Params = RecoveryParams
    requires = ("cup_shape", "agreement", "cup_choice")
    flag_codes = frozenset({"SPIKES_DISAGREE", "SPIKE_AMOUNT_MISSING"})

    def run(self, state: BatchState, params: RecoveryParams) -> None:
        agreement: AgreementParams = state.shared["agreement"]
        found: dict[str, list[tuple[Cell, Status]]] = {}
        for cell in state.cells.values():
            if not cell.usable or not cell.spikes:
                continue
            status = self._one(state, cell, params, agreement)
            found.setdefault(cell.analyte.code, []).append((cell, status))
        state.shared["recovery"] = found

    def _one(self, state: BatchState, cell: Cell, params: RecoveryParams, agreement) -> Status:
        added = state.batch.spike_added(cell.sample, cell.analyte)
        if added is None or added.unit != cell.unit:
            self.flag(cell, "SPIKE_AMOUNT_MISSING", "spike readings without a spike amount in their unit")
            self.trace(cell, status=Status.UNUSABLE.value, reason="no spike amount")
            return Status.UNUSABLE
        values = [cell.spikes[r].value.value * cell.spikes[r].dilution for r in sorted(cell.spikes)]
        if len(values) >= 2:
            agree, zone = replicates_agree(values[0], values[1], cell.mdl, agreement)  # D-34
            if not agree:
                self.flag(cell, "SPIKES_DISAGREE", f"spikes {values[0]} and {values[1]} ({zone.value} rule)")
                self.trace(cell, status=Status.UNUSABLE.value, reason="spikes disagree")
                return Status.UNUSABLE
            spiked = (values[0] + values[1]) / 2
        else:
            spiked = values[0]
        value = cell.value()
        if value is None:
            self.trace(cell, status=Status.UNUSABLE.value, reason="no sample value")
            return Status.UNUSABLE
        detail = {"spiked": text(spiked), "sample": text(value), "added": text(added.value)}
        if value > params.not_evaluable_multiple * added.value:
            self.trace(cell, status=Status.NOT_EVALUABLE.value, **detail)
            return Status.NOT_EVALUABLE
        cell.own_recovery = (spiked - value) / added.value
        self.trace(cell, status=Status.EVALUABLE.value, recovery=text(cell.own_recovery), **detail)
        return Status.EVALUABLE
