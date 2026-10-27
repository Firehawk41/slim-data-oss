"""The engine's inputs, working state and outputs.

`EvaluationInput` and `BatchEvaluation` are frozen. In between, steps work on a `BatchState` of
mutable `Cell`s (one per sample × analyte); the runner owns it and nothing outside the engine sees it.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import IntEnum
from typing import Any, Self

from pydantic import Field, model_validator

from slim_data.domain.base import Dec, Frozen
from slim_data.domain.enums import Qualifier, Verdict
from slim_data.domain.observations import Calibration, PrepBatch, Reading
from slim_data.domain.results import Flag, verdict_from_flags
from slim_data.domain.values import AnalyteRef, Limits, Quantity, SampleRef


class SampleLimits(Frozen):
    """Spec limits for one sample × analyte, looked up by the caller (DESIGN.md §5, D-60)."""

    sample: SampleRef
    analyte: AnalyteRef
    limits: Limits


class EvaluationInput(Frozen):
    """Everything one evaluation reads: the batch, its readings, the analyst's exclusions and the
    samples' spec limits."""

    batch: PrepBatch
    readings: tuple[Reading, ...] = Field(min_length=1)
    excluded_reading_ids: frozenset[int] = frozenset()
    limits: tuple[SampleLimits, ...] = ()
    calibrations: tuple[Calibration, ...] = ()  # of the runs the readings came from (D-90)
    # run ID -> run code (D-97): a run re-exported while it grows comes in as several imports, one code
    run_codes: tuple[tuple[int, str], ...] = ()

    @model_validator(mode="after")
    def _belongs_to_batch(self) -> Self:
        members = set(self.batch.samples)
        keys = [(entry.sample, entry.analyte) for entry in self.limits]
        if len(set(keys)) != len(keys):
            raise ValueError("two spec limits for the same sample and analyte")
        if any(sample not in members for sample, _ in keys):
            raise ValueError("spec limits for a sample that is not in the batch")
        for reading in self.readings:
            if not reading.role.of_batch and reading.sample not in members:
                raise ValueError(f"reading for {reading.sample}, which is not in batch {self.batch.code}")
            if reading.analyte.analysis_id != self.batch.analysis_id:
                raise ValueError(f"reading for {reading.analyte}, which is not this batch's analysis")
            if reading.role.is_qc:
                expected = self.batch.qc_expected(reading.role, reading.analyte)
                if expected is None:
                    raise ValueError(f"no expected value for the {reading.role.name} {reading.analyte.code}")
                if expected.expected.unit != reading.value.unit:
                    raise ValueError(
                        f"{reading.role.name} {reading.analyte.code} is in {reading.value.unit}; "
                        f"its expected value in {expected.expected.unit}"
                    )
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
    superseded: list[int] = field(default_factory=list)  # ids of older or excluded attempts set aside
    usable: bool = True  # False once a shape problem means later steps should leave it alone
    unit: str | None = None
    mdl: Decimal | None = None  # effective MDL of the cups (the larger; the kept cups' from reported_value@2)
    agree: bool | None = None
    selection: Selection = Selection.UNDECIDED
    chosen_replicate: int | None = None
    chosen_by: str | None = None  # the ladder rung that decided (D-32), e.g. "spike_witness"
    own_recovery: Decimal | None = None  # this sample's own spike recovery, if evaluable (D-36)
    recovery: Decimal | None = None  # the recovery reported beside the result: pooled (D-37)
    qualifier: Qualifier | None = None
    reported: Decimal | None = None  # rounded once (D-54); for BELOW_MDL, the effective MDL
    uncertainty: Decimal | None = None  # ± half-delta (D-55); None reads N/A
    limits: Limits | None = None
    flags: list[EmittedFlag] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)

    def cup_value(self, replicate: int) -> Decimal:
        reading = self.cups[replicate]
        return reading.value.value * reading.dilution

    def value(self) -> Decimal | None:
        """The unrounded value the result reports (D-54): the mean of agreeing cups, else the chosen
        cup; None when nothing is reportable."""
        if self.selection == Selection.AGREED:
            return (self.cup_value(1) + self.cup_value(2)) / 2
        if self.selection == Selection.CHOSEN:
            return self.cup_value(self.chosen_replicate)
        return None


@dataclass
class BatchState:
    batch: PrepBatch
    cells: dict[tuple[SampleRef, AnalyteRef], Cell]
    blanks: tuple[Reading, ...] = ()
    qc: tuple[Reading, ...] = ()  # check standards and CRMs (D-87)
    limits: dict[tuple[SampleRef, AnalyteRef], Limits] = field(default_factory=dict)
    calibrations: dict[tuple[int, AnalyteRef], Calibration] = field(default_factory=dict)  # (run, analyte)
    run_codes: dict[int, str] = field(default_factory=dict)  # run ID -> run code (D-97)
    shared: dict[str, Any] = field(default_factory=dict)  # facts one step leaves for a later one


class CellOutcome(Frozen):
    """What the engine decided for one sample × analyte, with the trace that explains it (D-16)."""

    sample: SampleRef
    analyte: AnalyteRef
    selection: Selection
    chosen_replicate: int | None
    raw: Dec | None = None  # unrounded, dilution-corrected (D-54): stored beside the reported value
    reported: Quantity | None = None
    qualifier: Qualifier | None = None
    uncertainty: Dec | None = None
    recovery: Dec | None = None  # fraction, e.g. 0.98; reported beside the result, never applied (D-38)
    limits: Limits | None = None  # snapshot of the spec limits used (D-04)
    flags: tuple[Flag, ...]
    trace: tuple[dict[str, Any], ...]

    @property
    def verdict(self) -> Verdict:
        """The worst flag decides (D-56); the trace lists every flag, so the reason is always there."""
        return verdict_from_flags(self.flags)


class BatchEvaluation(Frozen):
    ruleset_hash: str
    cells: tuple[CellOutcome, ...]

    def cell(self, sample: SampleRef, analyte: AnalyteRef) -> CellOutcome:
        for outcome in self.cells:
            if outcome.sample == sample and outcome.analyte == analyte:
                return outcome
        raise KeyError(f"{sample} {analyte}")
