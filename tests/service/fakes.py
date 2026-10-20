"""Fakes for the ports (DESIGN.md §5): reference data and staff, as the host would provide them."""

from dataclasses import dataclass, field

from tests.factories import ANA, ROB, SUE, sample


@dataclass
class FakeLookup:
    analyses: dict = field(default_factory=lambda: {"metals_icpms": 1, "anions_ic": 2})
    samples: set = field(default_factory=lambda: {sample(1), sample(2), sample(3)})
    unordered: set = field(default_factory=set)  # (sample, analysis_id) not ordered
    limits: dict = field(default_factory=dict)  # (sample, analyte) -> Limits

    def analysis_id(self, code):
        return self.analyses.get(code)

    def sample_exists(self, ref):
        return ref in self.samples

    def sample_requests(self, ref, analysis_id):
        return (ref, analysis_id) not in self.unordered

    def analyte_valid(self, a):
        return a.code in {"Cu", "Zn", "Fe", "Cl-"}

    def limits_for(self, ref, a):
        return self.limits.get((ref, a))


class FakeStaff:
    def find(self, staff_id):
        return {"ana": ANA, "rob": ROB, "sue": SUE}.get(staff_id)
