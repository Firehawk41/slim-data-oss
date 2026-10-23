"""The named demo scenarios (DESIGN.md §12.1), seeded through the real service from CSV files, so a
first visitor already sees history. All values are invented (D-05); each scenario has its own TR and
prep batch, so they don't affect each other.

    clean_pass        36 Elements: cups agree, spike recovered, blank clean: released, reported
    cups_disagree     10 Elements: Zn cups disagree; the direction prior believes the lower cup; over spec
    scale_fault       36 Elements: one cup scaled across six elements; the spikes pick the other cup
    pooled_spike      10 Elements: a sample's spike chose its cup, so it reports the others' recovery
    below_mdl         anions: F reported as "< MDL"
    rerun_exception   anions: SO4 recovery out of the window (RERUN); released by exception
    accepted          10 Elements: MDL missing (NEEDS_SUPERVISOR); a supervisor accepts it
    amended           assay: reported, then corrected (a titration redone) and reported as amended
    line_by_line      assay: exported twice while the run went on, with a titration rejected and redone

Four demo analyses (D-78), each with its ruleset: 10 and 36 Elements share the ICP-MS metals rules;
anions (F, Cl, NO3, SO4) by ion chromatography; and the assay of an acid in % w/w by duplicate
titration, which passes only when the two titrations' RSD is under 1 %.

The scenarios are host-agnostic (D-75): a `DemoHost` maps each demo analysis to its own analysis
code and ID, and gives them the people, the reference lookup and staff directory, and a received TR
for each. `seed` runs them on an in-memory host (slim-data's own tests and docs); the host app
passes its own (LabPortal: real TRs and demo personas). The host publishes the rulesets and policy
first (`publish_demo_rules`).

Not seeded: the solo sign-off (it needs its real 15-minute wait, D-68) and billing (invoicing).
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Protocol

from sqlalchemy.orm import Session

from slim_data.adapters.slim import SlimReferenceLookup, TrSampleInfo
from slim_data.domain.actor import Actor
from slim_data.domain.enums import IdentitySource, Role
from slim_data.domain.policy import DEMO_POLICY
from slim_data.domain.ports import ReferenceLookup, StaffDirectory
from slim_data.domain.results import ReportLine
from slim_data.domain.values import AnalyteRef, SampleRef
from slim_data.engine.ruleset import Ruleset
from slim_data.persistence.rulesets import publish_policy, publish_ruleset
from slim_data.rulesets import ruleset_text
from slim_data.service import workflow as wf
from slim_data.service.imports import import_run


@dataclass(frozen=True)
class DemoAnalysis:
    """How a demo analysis is measured: its ruleset file and what its result files say."""

    ruleset: str
    unit: str
    instrument: str
    prep_method: str
    mdl: str  # on every cup; "" = none (a titration has no MDL)


# The four demo analyses (D-78), by the key the scenarios use; a host maps each to its own analysis.
DEMO_ANALYSES: dict[str, DemoAnalysis] = {
    "10_elements": DemoAnalysis("demo_metals", "ppb", "ICP-MS 1", "microwave HNO3", "0.05"),
    "36_elements": DemoAnalysis("demo_metals", "ppb", "ICP-MS 1", "microwave HNO3", "0.05"),
    "anions": DemoAnalysis("demo_anions", "ppb", "IC 1", "dilution", "0.05"),
    "assay": DemoAnalysis("demo_assay", "%", "Titrator 1", "as received", ""),
}
ELEMENTS = ("Fe", "Cu", "Zn", "Ni", "Cr", "Mn", "F", "Cl")  # F and Cl are elements and anions
IONS = ("NO3", "SO4", "Assay")  # analyte codes that aren't element symbols
SPEC = {"Zn": 5.0}  # cups_disagree needs a customer + chemical with a Zn spec below its result


def persona(id: str, role: Role) -> Actor:
    """Demo personas sign in by choosing who they are, so they are ASSERTED (D-50)."""
    return Actor(id=id, display=id.title(), roles=frozenset({role}), identity_source=IdentitySource.ASSERTED)


ANA = persona("ana", Role.ANALYST)
ROB = persona("rob", Role.REVIEWER)
SUE = persona("sue", Role.SUPERVISOR)


class DemoHost(Protocol):
    """What a host gives the scenarios (D-75)."""

    analyses: Mapping[str, tuple[str, int]]  # DEMO_ANALYSES key -> (analysis code files name, ID)
    lookup: ReferenceLookup
    staff: StaffDirectory
    analyst: Actor  # enters and uploads the readings
    analyst_code: str  # how result files name the analyst (StaffDirectory.find)
    reviewer: Actor
    supervisor: Actor

    def new_tr(self, scenario: str, samples: int, spec: bool, analysis_id: int) -> str:
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

    analyses = {key: (key, number) for number, key in enumerate(DEMO_ANALYSES, start=1)}
    analyst, analyst_code, reviewer, supervisor = ANA, ANA.id, ROB, SUE

    def __init__(self) -> None:
        self.samples, self.specs, self.staff = _Samples(), {}, _Staff()
        self.lookup = SlimReferenceLookup(
            catalog={code: code for code, _ in self.analyses.values()},
            analyses=[_Named(id=number, name=code) for code, number in self.analyses.values()],
            elements=[_Named(id=i, symbol=s) for i, s in enumerate(ELEMENTS, start=1)],
            samples=self.samples,
            specs=lambda customer, chemical, location: self.specs.get((customer, chemical), {}),
            ions=IONS,
        )
        self._next = 101

    def new_tr(self, scenario: str, samples: int, spec: bool, analysis_id: int) -> str:
        tr, customer = f"TR{self._next:05d}", 2 if spec else 1
        self._next += 1
        if spec:
            self.specs[(customer, 1)] = SPEC
        for position in range(1, samples + 1):
            self.samples.known[SampleRef(tr_number=tr, position=position)] = TrSampleInfo(
                customer_id=customer, chemical_id=1, analysis_ids=frozenset({analysis_id})
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
    analysis: str  # a DEMO_ANALYSES key
    when: datetime
    tr: str
    batch: str
    rows: list = field(default_factory=list)

    def sample(self, position: int) -> SampleRef:
        return SampleRef(tr_number=self.tr, position=position)

    @property
    def analysis_id(self) -> int:
        return self.host.analyses[self.analysis][1]

    def analyte(self, code: str) -> AnalyteRef:
        return AnalyteRef(analysis_id=self.analysis_id, code=code)

    def row(self, position, code, role, replicate, value, mdl=None, spike="", rejected="", note=""):
        sample_id = "BLANK" if position is None else str(self.sample(position))
        mdl = DEMO_ANALYSES[self.analysis].mdl if mdl is None else mdl
        self.rows.append((sample_id, code, role, replicate, value, mdl, spike, rejected, note))

    def cups(self, position, code, c1, c2, mdl=None):
        self.row(position, code, "CUP", 1, c1, mdl)
        self.row(position, code, "CUP", 2, c2, mdl)

    def spike(self, position, code, value, added="20"):
        self.row(position, code, "SPIKE", 1, value, spike=added)

    def blank(self, code, value="0.010"):
        self.row(None, code, "BLANK", 1, value)

    def upload(self, run: str):
        h, stamp, how = self.host, self.when.isoformat(), DEMO_ANALYSES[self.analysis]
        code = h.analyses[self.analysis][0]
        lines = [HEADER] + [
            f"{s},{code},{c},{role},{rep},{v},{how.unit},{mdl},1,{run},{how.instrument},{h.analyst_code},"
            f"{stamp},{self.batch},{how.prep_method},{spike},{rej},{note}"
            for s, c, role, rep, v, mdl, spike, rej, note in self.rows
        ]
        content = ("\n".join(lines) + "\n").encode()
        import_run(self.session, content, f"{run}.csv", h.analyst, h.lookup, h.staff)
        return wf.evaluate_batch(self.session, self.batch, h.analyst, h.lookup)

    def review(self):
        wf.review_batch(self.session, self.batch, self.host.reviewer)

    def release(self, *positions):
        return [
            wf.release(self.session, self.sample(p), self.analysis_id, self.host.reviewer) for p in positions
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
    for i, code in enumerate(ELEMENTS[:6]):
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
    s.cups(1, "F", "0.012", "0.020")
    s.cups(2, "F", "3.00", "3.10")
    s.spike(2, "F", "23.0")
    s.blank("F")
    s.upload("RUN-0105")
    s.review_release(1, 2)


def _rerun_exception(s: _Scenario) -> None:
    s.cups(1, "SO4", "10.0", "10.0")
    s.spike(1, "SO4", "22.0")  # recovery 0.6: outside 75–125 %
    s.blank("SO4")
    s.upload("RUN-0106")
    s.review()
    wf.record_exception(
        s.session,
        s.sample(1),
        s.analyte("SO4"),
        s.host.supervisor,
        "Released without rerun",
        "no sample left; customer informed",
    )
    s.release(1)
    s.report((1, "SO4", 1))


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
    s.cups(1, "Assay", "69.52", "69.95")  # RSD 0.44 %: passes
    s.upload("RUN-0108")
    s.review_release(1)
    first = s.report((1, "Assay", 1))
    analyst = s.host.analyst
    wf.start_correction(
        s.session, s.sample(1), s.analyte("Assay"), analyst, "titration 2: burette misread; titrated again"
    )
    s.rows = []
    s.cups(1, "Assay", "69.52", "69.95")  # the same export again: skipped
    s.row(1, "Assay", "CUP", 2, "69.58")  # the second titration again: a new attempt, which counts
    s.upload("RUN-0108")
    s.review_release(1)
    s.report((1, "Assay", 2), amends=first)


def _line_by_line(s: _Scenario) -> None:
    s.cups(1, "Assay", "69.41", "69.47")
    s.upload("RUN-0109")  # first export: sample 1 is in
    s.review_release(1)
    s.row(2, "Assay", "CUP", 1, "68.90")
    s.row(2, "Assay", "CUP", 2, "70.10", rejected="yes", note="endpoint overshot")
    s.row(2, "Assay", "CUP", 2, "68.95")  # titrated again
    s.upload("RUN-0109")  # second export of the same run: sample 1 skipped, sample 2 added


# name -> (demo analysis, samples, needs a spec, run)
SCENARIOS: dict[str, tuple[str, int, bool, Callable]] = {
    "clean_pass": ("36_elements", 1, False, _clean_pass),
    "cups_disagree": ("10_elements", 1, True, _cups_disagree),
    "scale_fault": ("36_elements", 1, False, _scale_fault),
    "pooled_spike": ("10_elements", 3, False, _pooled_spike),
    "below_mdl": ("anions", 2, False, _below_mdl),
    "rerun_exception": ("anions", 1, False, _rerun_exception),
    "accepted": ("10_elements", 1, False, _accepted),
    "amended": ("assay", 1, False, _amended),
    "line_by_line": ("assay", 2, False, _line_by_line),
}


def run_scenarios(session: Session, host: DemoHost, today: datetime) -> dict[str, str]:
    """Runs every scenario on `host`, one a day up to yesterday; returns scenario -> TR number. The
    host has published the rulesets (`publish_demo_rules`) and the policy; the caller commits."""
    trs = {}
    for days_ago, (name, (analysis, samples, spec, run)) in zip(
        range(len(SCENARIOS), 0, -1), SCENARIOS.items(), strict=True
    ):
        tr = host.new_tr(name, samples, spec, host.analyses[analysis][1])
        run(_Scenario(session, host, analysis, today - timedelta(days=days_ago), tr, f"PB-{tr[2:]}"))
        trs[name] = tr
    return trs


def publish_demo_rules(session: Session, analyses: Mapping[str, tuple[str, int]], actor: Actor) -> None:
    """Publishes each demo analysis's ruleset, bound to the host's analysis ID (D-70 item 4, D-78), and
    the demo policy. Content-hashed: publishing again changes nothing."""
    for key, (_, analysis_id) in analyses.items():
        body = json.loads(ruleset_text(DEMO_ANALYSES[key].ruleset))
        body["analysis_id"] = analysis_id
        publish_ruleset(session, Ruleset.model_validate(body), actor)
    publish_policy(session, DEMO_POLICY, actor)


def seed(session: Session, today: datetime) -> tuple[dict[str, str], SlimReferenceLookup]:
    """Publishes the demo rulesets and policy and runs every scenario on an in-memory host; returns
    scenario -> TR number and the reference lookup the scenarios used. The caller commits."""
    host = InMemoryHost()
    publish_demo_rules(session, host.analyses, SUE)
    return run_scenarios(session, host, today), host.lookup
