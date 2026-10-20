"""What slim-data needs from outside (DESIGN.md §5), as protocols. slim-data never imports the host or
another module: the host passes implementations in (M6 adapts slim-domain; tests use fakes)."""

from typing import Protocol

from slim_data.domain.actor import Actor
from slim_data.domain.values import AnalyteRef, Limits, SampleRef


class ReferenceLookup(Protocol):
    def analysis_id(self, code: str) -> int | None:
        """slim-domain's analysis ID for a code such as `metals_icpms` (D-66); None if unknown."""

    def sample_exists(self, ref: SampleRef) -> bool: ...

    def sample_requests(self, ref: SampleRef, analysis_id: int) -> bool:
        """Was this analysis ordered for the sample?"""

    def analyte_valid(self, analyte: AnalyteRef) -> bool: ...

    def limits_for(self, ref: SampleRef, analyte: AnalyteRef) -> Limits | None:
        """The sample's spec limits, snapshotted onto the result (D-04, D-60); None if it has none."""


class StaffDirectory(Protocol):
    def find(self, staff_id: str) -> Actor | None:
        """The host's staff member with this ID (an import file names its analyst by ID, D-66)."""
