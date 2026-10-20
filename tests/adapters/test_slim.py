"""The ReferenceLookup adapter over slim-domain-shaped data (DESIGN.md §5)."""

from dataclasses import dataclass
from decimal import Decimal

from slim_data.adapters.slim import SlimReferenceLookup, TrSampleInfo
from slim_data.domain.values import Limits
from tests.factories import analyte, sample


@dataclass(frozen=True)
class Named:  # what slim-domain's Analysis and Element look like here
    id: int
    name: str = ""
    symbol: str = ""


class Samples:
    def find(self, ref):
        return {
            sample(1): TrSampleInfo(customer_id=7, chemical_id=3, analysis_ids=frozenset({1})),
            sample(2): TrSampleInfo(customer_id=7, chemical_id=None, analysis_ids=frozenset({1})),
        }.get(ref)


def lookup():
    return SlimReferenceLookup(
        catalog={"10_elements_icpms": "10 Elements", "retired": "Gone"},
        analyses=[Named(id=1, name="10 Elements")],
        elements=[Named(id=26, symbol="Fe"), Named(id=29, symbol="Cu")],
        samples=Samples(),
        specs=lambda customer, chemical, location: {"Cu": 0.5} if (customer, chemical) == (7, 3) else {},
        ions=["Cl-"],
    )


def test_codes_resolve_to_slim_domain_ids_by_name():
    assert lookup().analysis_id("10_elements_icpms") == 1
    assert lookup().analysis_id("retired") is None and lookup().analysis_id("nope") is None


def test_samples_analytes_and_orders():
    ref = lookup()
    assert ref.sample_exists(sample(1)) and not ref.sample_exists(sample(9))
    assert ref.sample_requests(sample(1), 1) and not ref.sample_requests(sample(1), 2)
    assert not ref.sample_requests(sample(9), 1)
    assert ref.analyte_valid(analyte("Cu")) and ref.analyte_valid(analyte("Cl-"))
    assert not ref.analyte_valid(analyte("Xx"))


def test_spec_limits_keep_their_written_digits():
    ref = lookup()
    assert ref.limits_for(sample(1), analyte("Cu")) == Limits(unit="ppb", high=Decimal("0.5"))
    assert ref.limits_for(sample(1), analyte("Fe")) is None  # no spec for Fe
    assert ref.limits_for(sample(2), analyte("Cu")) is None  # chemical not matched yet
    assert ref.limits_for(sample(9), analyte("Cu")) is None
