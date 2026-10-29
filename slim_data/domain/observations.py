"""What was observed: prep batches, instrument runs and raw readings (DESIGN.md §6.1).

Observations are facts. They are never edited; an analyst who distrusts a reading records an
exclusion instead.
"""

from decimal import Decimal
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from slim_data.domain.actor import Actor
from slim_data.domain.base import Dec, Frozen
from slim_data.domain.enums import ExclusionKind, ReadingRole, SourceKind
from slim_data.domain.values import AnalyteRef, Quantity, SampleRef


class SpikeAddition(Frozen):
    """The known amount added to one sample's spike aliquot for one analyte."""

    sample: SampleRef
    analyte: AnalyteRef
    added: Quantity

    @model_validator(mode="after")
    def _positive(self) -> Self:
        if self.added.value <= 0:
            raise ValueError("a spike adds a positive amount")
        return self


class QcExpectation(Frozen):
    """What a check standard or CRM should read for one analyte, and which material and lot it is
    (D-87). Batch data, like a spike amount; a reference-material register can supply it later."""

    role: ReadingRole
    analyte: AnalyteRef
    expected: Quantity
    material: str | None = Field(default=None, max_length=100)
    lot: str | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if not self.role.is_qc:
            raise ValueError("only a CHECK or CRM has an expected value")
        if self.expected.value <= 0:
            raise ValueError("an expected value is positive")
        return self


class CupDilution(Frozen):
    """A cup's dilution as the prep book recorded it (D-95): it beats the instrument file's."""

    sample: SampleRef
    role: ReadingRole  # CUP or SPIKE
    replicate: int = Field(ge=1)
    dilution: Dec = Field(gt=0)  # below 1 when the prep concentrates (D-93)

    @model_validator(mode="after")
    def _a_sample_cup(self) -> Self:
        if self.role not in (ReadingRole.CUP, ReadingRole.SPIKE):
            raise ValueError("prep records the dilution of a CUP or SPIKE")
        return self


class PrepBatch(Frozen):
    """Samples of one matrix prepped together: the unit of evaluation (D-07, D-24).

    The prep method is snapshotted here (D-15); the batch size limit is a ruleset parameter, so it
    is checked by the engine, not here.
    """

    id: int | None = None
    code: str = Field(min_length=1, max_length=50)
    analysis_id: int = Field(ge=1)
    prep_method: str = Field(min_length=1, max_length=100)
    # None for a QC-only batch (D-108): an instrument's own check run, blanks and check standards only.
    samples: tuple[SampleRef, ...] = ()
    spikes: tuple[SpikeAddition, ...] = ()
    qc: tuple[QcExpectation, ...] = ()
    dilutions: tuple[CupDilution, ...] = ()  # from slim-prep (D-95); none for a batch a file registered

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if len(set(self.samples)) != len(self.samples):
            raise ValueError("a sample appears twice in the batch")
        members = set(self.samples)
        seen = set()
        for spike in self.spikes:
            if spike.sample not in members:
                raise ValueError(f"spike for {spike.sample}, which is not in the batch")
            if spike.analyte.analysis_id != self.analysis_id:
                raise ValueError(f"spike for {spike.analyte}, which is not this batch's analysis")
            key = (spike.sample, spike.analyte)
            if key in seen:
                raise ValueError(f"two spike amounts for {spike.sample}, {spike.analyte}")
            seen.add(key)
        qc_keys = set()
        for entry in self.qc:
            if entry.analyte.analysis_id != self.analysis_id:
                raise ValueError(f"expected value for {entry.analyte}, which is not this batch's analysis")
            if (entry.role, entry.analyte) in qc_keys:
                raise ValueError(f"two expected values for {entry.role.name} {entry.analyte}")
            qc_keys.add((entry.role, entry.analyte))
        cups = set()
        for entry in self.dilutions:
            if entry.sample not in members:
                raise ValueError(f"dilution for {entry.sample}, which is not in the batch")
            key = (entry.sample, entry.role, entry.replicate)
            if key in cups:
                raise ValueError(f"two dilutions for {entry.sample} {entry.role.name} {entry.replicate}")
            cups.add(key)
        return self

    @property
    def qc_only(self) -> bool:
        return not self.samples

    def prep_dilution(self, sample: SampleRef, role: ReadingRole, replicate: int) -> Decimal | None:
        for entry in self.dilutions:
            if (entry.sample, entry.role, entry.replicate) == (sample, role, replicate):
                return entry.dilution
        return None

    def qc_expected(self, role: ReadingRole, analyte: AnalyteRef) -> QcExpectation | None:
        for entry in self.qc:
            if entry.role == role and entry.analyte == analyte:
                return entry
        return None

    def spike_added(self, sample: SampleRef, analyte: AnalyteRef) -> Quantity | None:
        for spike in self.spikes:
            if spike.sample == sample and spike.analyte == analyte:
                return spike.added
        return None


class SourceFile(Frozen):
    """Provenance of an import: the file and the reader that parsed it."""

    name: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reader: str = Field(min_length=1, max_length=50)  # e.g. "generic_csv/1"
    kind: SourceKind = SourceKind.CSV  # how the readings came in (D-88)


class Run(Frozen):
    """One instrument run = one imported file (D-25).

    `analyst` is who the file names (a claim, so ASSERTED); `imported_by` is who uploaded it, which
    may be someone else on the analyst's behalf. Four-eyes keeps both off the review (D-66).
    """

    id: int | None = None
    run_code: str = Field(min_length=1, max_length=50)
    instrument: str = Field(min_length=1, max_length=100)
    analyst: Actor
    imported_by: Actor
    analyzed_at: AwareDatetime
    source: SourceFile

    @property
    def entered_by(self) -> frozenset[str]:
        """Everyone who put these readings in: neither may review or release them (D-62, D-66)."""
        return frozenset({self.analyst.id, self.imported_by.id})


class Calibration(Frozen):
    """A run's calibration of one analyte, as the instrument reported it (D-89): what `CAL_FAILED`
    judges and the quality charts plot. Any figure the file didn't have is None."""

    analyte: AnalyteRef
    run_id: int | None = None  # set once stored: which run's readings it judges (D-90)
    unit: str = Field(min_length=1, max_length=20)  # of the LOD and BEC
    correlation: Dec | None = None  # r
    determination: Dec | None = None  # R²
    lod: Dec | None = None
    bec: Dec | None = None
    conditions: str | None = Field(default=None, min_length=1, max_length=50)  # e.g. plasma mode (D-106)


class Reading(Frozen):
    """One raw measurement (D-06): one row per reading, tagged with a role and a replicate number.

    A method blank, check standard or CRM is not a TR sample: it belongs to its prep batch, so a
    BLANK, CHECK or CRM reading has no sample, and every other reading must have one (D-64, D-87).
    """

    id: int | None = None
    run_id: int | None = None  # set once stored: the run it came from, for its calibration (D-90)
    sample: SampleRef | None
    analyte: AnalyteRef
    role: ReadingRole
    replicate: int = Field(ge=1)
    attempt: int = Field(
        default=1, ge=1
    )  # a re-measurement of the same cup; the newest good one counts (D-66)
    value: Quantity
    mdl: Quantity | None = None
    dilution: Dec = Decimal(1)
    # Instrument evidence, when the file has it (D-88); None = no evidence, never a fault.
    rsd: Dec | None = None  # the replicate RSD, in %
    istd_recovery: Dec | None = None  # the internal standard's recovery, in %
    # Where it was run (D-97): its line in the run's file, so check standards can bracket it. None for
    # readings imported before 0009: they are judged batch-wide, as before.
    sequence: int | None = Field(default=None, ge=1)
    # The instrument conditions it was measured under, e.g. the plasma mode (D-106); None = not said.
    conditions: str | None = Field(default=None, min_length=1, max_length=50)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.role.of_batch != (self.sample is None):
            raise ValueError("a BLANK, CHECK or CRM reading has no sample; every other reading needs one")
        if self.mdl is not None:
            if self.mdl.unit != self.value.unit:
                raise ValueError("MDL must be in the same unit as the value")
            if self.mdl.value <= 0:
                raise ValueError("MDL must be positive")
        if self.dilution <= 0:  # below 1 when the prep concentrates the sample, e.g. evaporation (D-93)
            raise ValueError("dilution factor must be positive")
        if self.rsd is not None and self.rsd < 0:
            raise ValueError("RSD can't be negative")
        if self.istd_recovery is not None and self.istd_recovery <= 0:
            raise ValueError("internal standard recovery must be positive")
        return self

    @property
    def effective_mdl(self) -> Quantity | None:
        """MDL × dilution (D-54): the floor the reported value is compared against."""
        if self.mdl is None:
            return None
        return Quantity(value=self.mdl.value * self.dilution, unit=self.mdl.unit)


class ReadingExclusion(Frozen):
    """An analyst marks a reading bad (D-32 rung 1), or puts it back (RESTORED). An event; the
    reading itself never changes, and neither does an earlier exclusion."""

    reading_id: int
    kind: ExclusionKind = ExclusionKind.EXCLUDED
    reason: str = Field(min_length=1, max_length=500)
    actor: Actor
    at: AwareDatetime | None = None  # set from the database clock when stored (D-21)
