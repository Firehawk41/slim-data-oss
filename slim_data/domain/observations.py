"""What was observed: prep batches, instrument runs and raw readings (DESIGN.md §6.1).

Observations are facts. They are never edited; an analyst who distrusts a reading records an
exclusion instead.
"""

from decimal import Decimal
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from slim_data.domain.actor import Actor
from slim_data.domain.base import Dec, Frozen
from slim_data.domain.enums import ExclusionKind, ReadingRole
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


class PrepBatch(Frozen):
    """Samples of one matrix prepped together: the unit of evaluation (D-07, D-24).

    The prep method is snapshotted here (D-15); the batch size limit is a ruleset parameter, so it
    is checked by the engine, not here.
    """

    id: int | None = None
    code: str = Field(min_length=1, max_length=50)
    analysis_id: int = Field(ge=1)
    prep_method: str = Field(min_length=1, max_length=100)
    samples: tuple[SampleRef, ...] = Field(min_length=1)
    spikes: tuple[SpikeAddition, ...] = ()

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
        return self

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


class Reading(Frozen):
    """One raw measurement (D-06): one row per reading, tagged with a role and a replicate number.

    A method blank is not a TR sample: it belongs to its prep batch, so a BLANK reading has no
    sample, and every other reading must have one (D-64).
    """

    id: int | None = None
    sample: SampleRef | None
    analyte: AnalyteRef
    role: ReadingRole
    replicate: int = Field(ge=1)
    value: Quantity
    mdl: Quantity | None = None
    dilution: Dec = Decimal(1)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.role == ReadingRole.BLANK) != (self.sample is None):
            raise ValueError("a BLANK reading has no sample; every other reading needs one")
        if self.mdl is not None:
            if self.mdl.unit != self.value.unit:
                raise ValueError("MDL must be in the same unit as the value")
            if self.mdl.value <= 0:
                raise ValueError("MDL must be positive")
        if self.dilution < 1:
            raise ValueError("dilution factor must be at least 1")
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
