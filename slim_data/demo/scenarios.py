"""The named demo scenarios (DESIGN.md §12.1), seeded through the real service from CSV files, so a
first visitor already sees history. All values are invented (D-05); each scenario has its own TR and
prep batch, so they don't affect each other.

    clean_pass        cups agree, spike recovered, blank clean: reviewed, released, reported
    cups_disagree     Zn cups disagree; the direction prior believes the lower cup; over spec (INFO)
    scale_fault       one cup scaled across six elements; the spikes pick the other cup
    pooled_spike      a sample's spike chose its cup, so it reports the others' recovery
    below_mdl         reported as "< MDL"
    rerun_exception   recovery out of the window (RERUN); a supervisor releases it by exception
    accepted          MDL missing (NEEDS_SUPERVISOR); a supervisor accepts it
    amended           reported, then corrected (a cup excluded) and reported again as amended
    line_by_line      exported twice while the run went on, with a cup rejected and rerun

The scenarios are host-agnostic (D-75): a `DemoHost` gives them the analysis, the people, the
reference lookup and staff directory, and makes a received TR for each. `seed` runs them on an
in-memory host (slim-data's own tests and docs); the host app passes its own (LabPortal: real TRs
and demo personas). The host publishes the ruleset and policy first.

Not seeded: the solo sign-off (it needs its real 15-minute wait, D-68) and billing (invoicing).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Protocol

from sqlalchemy.orm import Session

from slim_data.adapters.slim import SlimReferenceLookup, TrSampleInfo
from slim_data.domain.actor import Actor
from slim_data.domain.enums import IdentitySource, Role
from slim_data.domain.observations import ReadingExclusion
from slim_data.domain.policy import DEMO_POLICY
from slim_data.domain.ports import ReferenceLookup, StaffDirectory
from slim_data.domain.results import ReportLine
from slim_data.domain.values import AnalyteRef, SampleRef
from slim_data.engine.ruleset import Ruleset
from slim_data.persistence.observations import evaluation_input, exclude_reading
from slim_data.persistence.rulesets import publish_policy, publish_ruleset
from slim_data.rulesets import ruleset_text
from slim_data.service import workflow as wf
from slim_data.service.imports import import_run

ANALYSIS = "metals_icpms"
ANALYSIS_ID = 1
ELEMENTS = ("Fe", "Cu", "Zn", "Ni", "Cr", "Mn")
SPEC = {"Zn": 5.0}  # cups_disagree needs a customer + chemical with a Zn spec below its result


def persona(id: str, role: Role) -> Actor:
    """Demo personas sign in by choosing who they are, so they are ASSERTED (D-50)."""
    return Actor(id=id, display=id.title(), roles=frozenset({role}), identity_source=IdentitySource.ASSERTED)


ANA = persona("ana", Role.ANALYST)
ROB = persona("rob", Role.REVIEWER)
SUE = persona("sue", Role.SUPERVISOR)


class DemoHost(Protocol):
    """What a host gives the scenarios (D-75)."""

    analysis_code: str  # the analysis code result files name
    analysis_id: int
    lookup: ReferenceLookup
    staff: StaffDirectory
    analyst: Actor  # enters and uploads the readings
    analyst_code: str  # how result files name the analyst (StaffDirectory.find)
    reviewer: Actor
    supervisor: Actor

    def new_tr(self, scenario: str, samples: int, spec: bool) -> str:
        """A received TR with `samples` samples ordering the analysis; `spec`: its customer and
        chemical have a spec for an analyte the scenario reports above it (SPEC). Returns its number."""
        ...

    def report_sha256(self, session: Session, tr_number: str, lines: tuple[ReportLine, ...]) -> str:
        """The hash of the report file the host would issue with these lines."""
        ...


@dataclass(frozen=True)
class _Named:
    id: int
    name: str = ""
    symbol: str = ""


class _Samples:
    def __init__(self) -> None:
        self.known: dict[SampleRef, TrSampleInfo] = {}

    def find(self, ref: SampleRef) -> TrSampleInfo | None:
        return self.known.get(ref)


class _Staff:
    def find(self, staff_id: str) -> Actor | None:
        return {p.id: p for p in (ANA, ROB, SUE)}.get(staff_id)


class InMemoryHost:
    """The demo's reference data through the same adapter a host uses, held in memory. TRs are
    numbered TR00101, TR00102, ... in scenario order."""

    analysis_code, analysis_id = ANALYSIS, ANALYSIS_ID
    analyst, analyst_code, reviewer, supervisor = ANA, ANA.id, ROB, SUE

    def __init__(self) -> None:
        self.samples, self.specs, self.staff = _Samples(), {}, _Staff()
        self.lookup = SlimReferenceLookup(
            catalog={ANALYSIS: "Metals ICP-MS"},
            analyses=[_Named(id=ANALYSIS_ID, name="Metals ICP-MS")],
            elements=[_Named(id=i, symbol=s) for i, s in enumerate(ELEMENTS, start=1)],
            samples=self.samples,
            specs=lambda customer, chemical, location: self.specs.get((customer, chemical), {}),
        )
        self._next = 101

    def new_tr(self, scenario: str, samples: int, spec: bool) -> str:
        tr, customer = f"TR{self._next:05d}", 2 if spec else 1
        self._next += 1
        if spec:
            self.specs[(customer, 1)] = SPEC
        for position in range(1, samples + 1):
            self.samples.known[SampleRef(tr_number=tr, position=position)] = TrSampleInfo(
                customer_id=customer, chemical_id=1, analysis_ids=frozenset({ANALYSIS_ID})
            )
        return tr

    def report_sha256(self, session: Session, tr_number: str, lines: tuple[ReportLine, ...]) -> str:
        return sha256(
            f"{tr_number}/{[(str(li.sample), li.analyte.code, li.version) for li in lines]}".encode()
        ).hexdigest()


HEADER = (
    "lab_sample_id,analysis_code,analyte,role,replicate,value,unit,mdl,dilution_factor,run_code,"
    "instrument,analyst,analyzed_at,prep_batch,prep_method,spike_added,rejected,note"
)


@dataclass
class _Scenario:
    session: Session
    host: DemoHost
    when: datetime
    tr: str
    batch: str
    rows: list = field(default_factory=list)

    def sample(self, position: int) -> SampleRef:
        return SampleRef(tr_number=self.tr, position=position)

    def analyte(self, code: str) -> AnalyteRef:
        return AnalyteRef(analysis_id=self.host.analysis_id, code=code)

    def row(self, position, code, role, replicate, value, mdl="0.05", spike="", rejected="", note=""):
        sample_id = "BLANK" if position is None else str(self.sample(position))
        self.rows.append((sample_id, code, role, replicate, value, mdl, spike, rejected, note))

    def cups(self, position, code, c1, c2, mdl="0.05"):
        self.row(position, code, "CUP", 1, c1, mdl)
        self.row(position, code, "CUP", 2, c2, mdl)

    def spike(self, position, code, value, added="20"):
        self.row(position, code, "SPIKE", 1, value, spike=added)

    def blank(self, code, value="0.010"):
        self.row(None, code, "BLANK", 1, value)

    def upload(self, run: str):
        h, stamp = self.host, self.when.isoformat()
        lines = [HEADER] + [
            f"{s},{h.analysis_code},{c},{role},{rep},{v},ppb,{mdl},1,{run},ICP-MS 1,{h.analyst_code},{stamp},"
            f"{self.batch},microwave HNO3,{spike},{rej},{note}"
            for s, c, role, rep, v, mdl, spike, rej, note in self.rows
        ]
        content = ("\n".join(lines) + "\n").encode()
        import_run(self.session, content, f"{run}.csv", h.analyst, h.lookup, h.staff)
        return wf.evaluate_batch(self.session, self.batch, h.analyst, h.lookup)

    def review(self):
        wf.review_batch(self.session, self.batch, self.host.reviewer)

    def release(self, *positions):
        return [
            wf.release(self.session, self.sample(p), self.host.analysis_id, self.host.reviewer)
            for p in positions
        ]

    def review_release(self, *positions):
        self.review()
        return self.release(*positions)

    def report(self, *lines: tuple[int, str, int], amends: int | None = None) -> int:
        report_lines = tuple(
            ReportLine(sample=self.sample(p), analyte=self.analyte(c), version=v) for p, c, v in lines
        )
        digest = self.host.report_sha256(self.session, self.tr, report_lines)
        return wf.record_issue(
            self.session, self.tr, report_lines, digest, self.host.reviewer, amends
        ).issue_id


def _clean_pass(s: _Scenario) -> None:
    for code in ("Cu", "Zn"):
        s.cups(1, code, "10.00", "10.20")
        s.spike(1, code, "29.9")
        s.blank(code)
    s.upload("RUN-0101")
    s.review_release(1)
    s.report((1, "Cu", 1), (1, "Zn", 1))


def _cups_disagree(s: _Scenario) -> None:
    s.cups(1, "Zn", "10.0", "14.0")  # Zn is contamination-prone: believe the lower cup
    s.spike(1, "Zn", "32.0")  # w = 12.0, midway between the cups: the spike can't decide
    s.blank("Zn")
    s.upload("RUN-0102")
    s.review_release(1)


def _scale_fault(s: _Scenario) -> None:
    for i, code in enumerate(ELEMENTS):
        base = 10 + 2 * i
        s.cups(1, code, f"{base}.00", str(Decimal(base) * Decimal("1.30")))  # cup 2 reads 30 % high
        s.spike(1, code, f"{base + 20}.0")  # each spike witnesses cup 1
        s.blank(code)
    s.upload("RUN-0103")


def _pooled_spike(s: _Scenario) -> None:
    s.cups(1, "Cu", "10.0", "14.0")
    s.spike(1, "Cu", "30.2")  # chooses cup 1, so sample 1 reports the others' recovery
    for position, spiked in ((2, "29.6"), (3, "30.1")):
        s.cups(position, "Cu", "10.0", "10.1")
        s.spike(position, "Cu", spiked)
    s.blank("Cu")
    s.upload("RUN-0104")
    s.review_release(1, 2, 3)


def _below_mdl(s: _Scenario) -> None:
    s.cups(1, "Cu", "0.012", "0.020")
    s.cups(2, "Cu", "3.00", "3.10")
    s.spike(2, "Cu", "23.0")
    s.blank("Cu")
    s.upload("RUN-0105")
    s.review_release(1, 2)


def _rerun_exception(s: _Scenario) -> None:
    s.cups(1, "Cu", "10.0", "10.0")
    s.spike(1, "Cu", "22.0")  # recovery 0.6: outside 75–125 %
    s.blank("Cu")
    s.upload("RUN-0106")
    s.review()
    wf.record_exception(
        s.session,
        s.sample(1),
        s.analyte("Cu"),
        s.host.supervisor,
        "Released without rerun",
        "no sample left; customer informed",
    )
    s.release(1)
    s.report((1, "Cu", 1))


def _accepted(s: _Scenario) -> None:
    s.cups(1, "Cu", "8.00", "8.10", mdl="")
    s.spike(1, "Cu", "27.8")
    s.blank("Cu")
    s.upload("RUN-0107")
    s.review()
    wf.accept(
        s.session, s.sample(1), s.analyte("Cu"), s.host.supervisor, "MDL taken from the method validation"
    )
    s.release(1)


def _amended(s: _Scenario) -> None:
    s.cups(1, "Cu", "10.0", "11.5")
    s.spike(1, "Cu", "30.0")
    s.blank("Cu")
    s.upload("RUN-0108")
    s.review_release(1)
    first = s.report((1, "Cu", 1))
    analyst = s.host.analyst
    wf.start_correction(s.session, s.sample(1), s.analyte("Cu"), analyst, "cup 2 had a bubble; excluded")
    cup_2 = [
        r for r in evaluation_input(s.session, s.batch).readings if r.replicate == 2 and r.role.name == "CUP"
    ]
    exclude_reading(
        s.session, ReadingExclusion(reading_id=cup_2[0].id, reason="bubble in cup 2", actor=analyst)
    )
    wf.evaluate_batch(s.session, s.batch, analyst, s.host.lookup)
    s.review_release(1)
    s.report((1, "Cu", 2), amends=first)


def _line_by_line(s: _Scenario) -> None:
    s.cups(1, "Cu", "10.00", "10.20")
    s.spike(1, "Cu", "29.9")
    s.blank("Cu")
    s.upload("RUN-0109")  # first export: sample 1 is in
    s.review_release(1)
    s.row(2, "Cu", "CUP", 1, "5.10")
    s.row(2, "Cu", "CUP", 2, "7.90", rejected="yes", note="drift check failed")
    s.row(2, "Cu", "CUP", 2, "5.20")  # rerun of the rejected cup
    s.upload("RUN-0109")  # second export of the same run: sample 1 skipped, sample 2 added


# name -> (samples, needs a spec, run)
SCENARIOS: dict[str, tuple[int, bool, Callable]] = {
    "clean_pass": (1, False, _clean_pass),
    "cups_disagree": (1, True, _cups_disagree),
    "scale_fault": (1, False, _scale_fault),
    "pooled_spike": (3, False, _pooled_spike),
    "below_mdl": (2, False, _below_mdl),
    "rerun_exception": (1, False, _rerun_exception),
    "accepted": (1, False, _accepted),
    "amended": (1, False, _amended),
    "line_by_line": (2, False, _line_by_line),
}


def run_scenarios(session: Session, host: DemoHost, today: datetime) -> dict[str, str]:
    """Runs every scenario on `host`, one a day up to yesterday; returns scenario -> TR number. The
    host has published the ruleset (for its analysis) and the policy; the caller commits."""
    trs = {}
    for days_ago, (name, (samples, spec, run)) in zip(
        range(len(SCENARIOS), 0, -1), SCENARIOS.items(), strict=True
    ):
        tr = host.new_tr(name, samples, spec)
        run(_Scenario(session, host, today - timedelta(days=days_ago), tr, f"PB-{tr[2:]}"))
        trs[name] = tr
    return trs


def seed(session: Session, today: datetime) -> tuple[dict[str, str], SlimReferenceLookup]:
    """Publishes the demo ruleset and policy and runs every scenario on an in-memory host; returns
    scenario -> TR number and the reference lookup the scenarios used. The caller commits."""
    publish_ruleset(session, Ruleset.model_validate_json(ruleset_text("demo_metals")), SUE)
    publish_policy(session, DEMO_POLICY, SUE)
    host = InMemoryHost()
    return run_scenarios(session, host, today), host.lookup
