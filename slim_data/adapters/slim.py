"""ReferenceLookup over the SLIM ecosystem (DESIGN.md §5), without importing it.

Where the reference data lives today:
* analysis codes: the host's analysis catalog (LabPortal's `analysis_catalog`: code -> SLIM analysis
  name), resolved to slim-domain's analysis IDs by name;
* elements: slim-domain's `elements` (symbols); ion codes (e.g. `Cl-`) are passed in;
* TR samples (customer, chemical, location, ordered analyses): the TR v2 tables, in the portal until
  D-45 moves them into slim-domain, so the host passes a `TrSamples` reader;
* spec limits: slim-domain's `SpecificationService.get_specs(customer, chemical, location)`, which
  gives element -> upper limit as floats.

The adapter takes these as plain objects and callables (duck typing: anything with `.id`/`.name`,
`.symbol`), so slim-data never depends on slim-domain or the host; the host builds it at start-up.
"""

from collections.abc import Callable, Iterable, Mapping
from decimal import Decimal
from typing import Protocol

from slim_data.domain.base import Frozen
from slim_data.domain.values import AnalyteRef, Limits, SampleRef


class TrSampleInfo(Frozen):
    customer_id: int
    chemical_id: int | None  # None until the lab matches the customer's chemical to SLIM
    location: str = ""
    analysis_ids: frozenset[int] = frozenset()


class TrSamples(Protocol):
    def find(self, ref: SampleRef) -> TrSampleInfo | None: ...


SpecsFor = Callable[[int, int, str], Mapping[str, float]]


class SlimReferenceLookup:
    def __init__(
        self,
        catalog: Mapping[str, str],
        analyses: Iterable,
        elements: Iterable,
        samples: TrSamples,
        specs: SpecsFor,
        ions: Iterable[str] = (),
        spec_unit: str = "ppb",
    ) -> None:
        by_name = {a.name: a.id for a in analyses}
        self._analysis_ids = {code: by_name[name] for code, name in catalog.items() if name in by_name}
        self._analytes = {e.symbol for e in elements} | set(ions)
        self._samples = samples
        self._specs = specs
        self._spec_unit = spec_unit

    def analysis_id(self, code: str) -> int | None:
        return self._analysis_ids.get(code)

    def sample_exists(self, ref: SampleRef) -> bool:
        return self._samples.find(ref) is not None

    def sample_requests(self, ref: SampleRef, analysis_id: int) -> bool:
        info = self._samples.find(ref)
        return info is not None and analysis_id in info.analysis_ids

    def analyte_valid(self, analyte: AnalyteRef) -> bool:
        """slim-domain has no analysis -> element table, so any known element or ion is valid."""
        return analyte.code in self._analytes

    def limits_for(self, ref: SampleRef, analyte: AnalyteRef) -> Limits | None:
        info = self._samples.find(ref)
        if info is None or info.chemical_id is None:
            return None
        limit = self._specs(info.customer_id, info.chemical_id, info.location).get(analyte.code)
        if limit is None:
            return None
        # slim-domain stores specs as floats; str() gives back the digits as written (0.5 -> "0.5").
        return Limits(unit=self._spec_unit, high=Decimal(str(limit)))
