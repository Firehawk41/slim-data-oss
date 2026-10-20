"""The engine's inputs, working state and outputs.

`EvaluationInput` and `BatchEvaluation` are frozen. In between, steps work on a `BatchState` of
mutable `Cell`s (one per sample × analyte); the runner owns it and nothing outside the engine sees it.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import IntEnum
from typing import Any, Self

from pydantic import Field, model_validator

from slim_data.domain.base import Frozen
from slim_data.domain.enums import ReadingRole
from slim_data.domain.observations import PrepBatch, Reading
from slim_data.domain.results import Flag
from slim_data.domain.values import AnalyteRef, SampleRef


class EvaluationInput(Frozen):
    """Everything one evaluation reads: the batch, its readings and the analyst's exclusions."""

    batch: PrepBatch
    readings: tuple[Reading, ...] = Field(min_length=1)
    excluded_reading_ids: frozenset[int] = frozenset()

    @model_validator(mode="after")
    def _belongs_to_batch(self) -> Self:
        members = set(self.batch.samples)
        for reading in self.readings:
            if reading.role != ReadingRole.BLANK and reading.sample not in members:
                raise ValueError(f"reading for {reading.sample}, which is not in batch {self.batch.code}")
            if reading.analyte.analysis_id != self.batch.analysis_id:
                raise ValueError(f"reading for {reading.analyte}, which is not this batch's analysis")
        known_ids = {r.id for r in self.readings if r.id is not None}
        unknown = self.excluded_reading_ids - known_ids
        if unknown:
            raise ValueError(f"exclusions for readings not in this input: {sorted(unknown)}")
        return self


class Selection(IntEnum):
    """Which number the cell will report, as decided so far."""

    UNDECIDED = 0
    AGREED = 1  # mean of the agreeing cups
    CHOSEN = 2  # one cup, picked by the evidence ladder
    NONE = 3  # nothing reportable (e.g. both cups excluded)


@dataclass
class EmittedFlag:
    code: str
    step: str
    message: str = ""


@dataclass
class Cell:
    """One sample × analyte while the steps work on it. Values here are effective: value × dilution."""

    sample: SampleRef
    analyte: AnalyteRef
    cups: dict[int, Reading] = field(default_factory=dict)  # replicate -> reading
    spikes: dict[int, Reading] = field(default_factory=dict)
    excluded: frozenset[int] = frozenset()  # replicates of cups the analyst excluded
    usable: bool = True  # False once a shape problem means later steps should leave it alone
    unit: str | None = None
    mdl: Decimal | None = None  # effective MDL of the cups (the larger, if they differ)
    agree: bool | None = None
    selection: Selection = Selection.UNDECIDED
    chosen_replicate: int | None = None
    chosen_by: str | None = None  # the ladder rung that decided (D-32), e.g. "spike_witness"
    flags: list[EmittedFlag] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)

    def cup_value(self, replicate: int) -> Decimal:
        reading = self.cups[replicate]
        return reading.value.value * reading.dilution


@dataclass
class BatchState:
    batch: PrepBatch
    cells: dict[tuple[SampleRef, AnalyteRef], Cell]
    blanks: tuple[Reading, ...] = ()
    shared: dict[str, Any] = field(default_factory=dict)  # facts one step leaves for a later one


class CellOutcome(Frozen):
    """What the engine decided for one sample × analyte, with the trace that explains it (D-16)."""

    sample: SampleRef
    analyte: AnalyteRef
    selection: Selection
    chosen_replicate: int | None
    flags: tuple[Flag, ...]
    trace: tuple[dict[str, Any], ...]


class BatchEvaluation(Frozen):
    ruleset_hash: str
    cells: tuple[CellOutcome, ...]

    def cell(self, sample: SampleRef, analyte: AnalyteRef) -> CellOutcome:
        for outcome in self.cells:
            if outcome.sample == sample and outcome.analyte == analyte:
                return outcome
        raise KeyError(f"{sample} {analyte}")
