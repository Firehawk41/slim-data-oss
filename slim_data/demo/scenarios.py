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

Not seeded: the solo sign-off (it needs its real 15-minute wait, D-68) and billing (invoicing).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256

from sqlalchemy.orm import Session

from slim_data.adapters.slim import SlimReferenceLookup, TrSampleInfo
from slim_data.domain.actor import Actor
from slim_data.domain.enums import IdentitySource, Role
from slim_data.domain.observations import ReadingExclusion
from slim_data.domain.policy import DEMO_POLICY
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


def persona(id: str, role: Role) -> Actor:
    """Demo personas sign in by choosing who they are, so they are ASSERTED (D-50)."""
    return Actor(id=id, display=id.title(), roles=frozenset({role}), identity_source=IdentitySource.ASSERTED)


ANA = persona("ana", Role.ANALYST)
ROB = persona("rob", Role.REVIEWER)
SUE = persona("sue", Role.SUPERVISOR)
STAFF = {p.id: p for p in (ANA, ROB, SUE)}


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
        return STAFF.get(staff_id)


def demo_reference() -> tuple[SlimReferenceLookup, _Samples, dict]:
    """The demo's reference data, through the same adapter the host uses (in memory here)."""
    samples, specs = _Samples(), {}
    lookup = SlimReferenceLookup(
        catalog={ANALYSIS: "Metals ICP-MS"},
        analyses=[_Named(id=ANALYSIS_ID, name="Metals ICP-MS")],
        elements=[_Named(id=i, symbol=s) for i, s in enumerate(ELEMENTS, start=1)],
        samples=samples,
        specs=lambda customer, chemical, location: specs.get((customer, chemical), {}),
    )
    return lookup, samples, specs


HEADER = (
    "lab_sample_id,analysis_code,analyte,role,replicate,value,unit,mdl,dilution_factor,run_code,"
    "instrument,analyst,analyzed_at,prep_batch,prep_method,spike_added,rejected,note"
)


@dataclass
class _Scenario:
    session: Session
    lookup: SlimReferenceLookup
    samples: _Samples
    when: datetime
    tr: str
    batch: str
    specs: dict
    customer: int = 1
    rows: list = field(default_factory=list)

    def sample(self, position: int) -> SampleRef:
        ref = SampleRef(tr_number=self.tr, position=position)
        self.samples.known[ref] = TrSampleInfo(
            customer_id=self.customer, chemical_id=1, analysis_ids=frozenset({ANALYSIS_ID})
        )
        return ref

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

    def upload(self, run: str, analyst: Actor = ANA, uploader: Actor = ANA, rows=None):
        stamp = self.when.isoformat()
        lines = [HEADER] + [
            f"{s},{ANALYSIS},{c},{role},{rep},{v},ppb,{mdl},1,{run},ICP-MS 1,{analyst.id},{stamp},"
            f"{self.batch},microwave HNO3,{spike},{rej},{note}"
            for s, c, role, rep, v, mdl, spike, rej, note in (self.rows if rows is None else rows)
        ]
        content = ("\n".join(lines) + "\n").encode()
        import_run(self.session, content, f"{run}.csv", uploader, self.lookup, _Staff())
        return wf.evaluate_batch(self.session, self.batch, uploader, self.lookup)

    def review_release(self, *positions):
        wf.review_batch(self.session, self.batch, ROB)
        return [
            wf.release(self.session, SampleRef(tr_number=self.tr, position=p), ANALYSIS_ID, ROB)
            for p in positions
        ]

    def report(self, *lines: tuple[int, str, int], amends: int | None = None) -> int:
        report_lines = tuple(
            ReportLine(sample=SampleRef(tr_number=self.tr, position=p), analyte=_analyte(c), version=v)
            for p, c, v in lines
        )
        digest = sha256(f"{self.tr}/{lines}/{amends}".encode()).hexdigest()
        return wf.record_issue(self.session, self.tr, report_lines, digest, ROB, amends).issue_id


def _analyte(code: str) -> AnalyteRef:
    return AnalyteRef(analysis_id=ANALYSIS_ID, code=code)


def _clean_pass(s: _Scenario) -> None:
    for code in ("Cu", "Zn"):
        s.cups(1, code, "10.00", "10.20")
        s.spike(1, code, "29.9")
        s.blank(code)
    s.upload("RUN-0101")
    s.review_release(1)
    s.report((1, "Cu", 1), (1, "Zn", 1))


def _cups_disagree(s: _Scenario) -> None:
    s.customer = 2  # a customer with a Zn spec of 5 ppb
    s.specs[(2, 1)] = {"Zn": 5.0}
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
    wf.review_batch(s.session, s.batch, ROB)
    wf.record_exception(
        s.session,
        s.sample(1),
        _analyte("Cu"),
        SUE,
        "Released without rerun",
        "no sample left; customer informed",
    )
    wf.release(s.session, s.sample(1), ANALYSIS_ID, ROB)
    s.report((1, "Cu", 1))


def _accepted(s: _Scenario) -> None:
    s.cups(1, "Cu", "8.00", "8.10", mdl="")
    s.spike(1, "Cu", "27.8")
    s.blank("Cu")
    s.upload("RUN-0107")
    wf.review_batch(s.session, s.batch, ROB)
    wf.accept(s.session, s.sample(1), _analyte("Cu"), SUE, "MDL taken from the method validation")
    wf.release(s.session, s.sample(1), ANALYSIS_ID, ROB)


def _amended(s: _Scenario) -> None:
    s.cups(1, "Cu", "10.0", "11.5")
    s.spike(1, "Cu", "30.0")
    s.blank("Cu")
    s.upload("RUN-0108")
    s.review_release(1)
    first = s.report((1, "Cu", 1))
    wf.start_correction(s.session, s.sample(1), _analyte("Cu"), ANA, "cup 2 had a bubble; excluded")
    cup_2 = [
        r for r in evaluation_input(s.session, s.batch).readings if r.replicate == 2 and r.role.name == "CUP"
    ]
    exclude_reading(s.session, ReadingExclusion(reading_id=cup_2[0].id, reason="bubble in cup 2", actor=ANA))
    wf.evaluate_batch(s.session, s.batch, ANA, s.lookup)
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


SCENARIOS: dict[str, tuple[str, Callable]] = {
    "clean_pass": ("TR00101", _clean_pass),
    "cups_disagree": ("TR00102", _cups_disagree),
    "scale_fault": ("TR00103", _scale_fault),
    "pooled_spike": ("TR00104", _pooled_spike),
    "below_mdl": ("TR00105", _below_mdl),
    "rerun_exception": ("TR00106", _rerun_exception),
    "accepted": ("TR00107", _accepted),
    "amended": ("TR00108", _amended),
    "line_by_line": ("TR00109", _line_by_line),
}


def seed(session: Session, today: datetime) -> tuple[dict[str, str], SlimReferenceLookup]:
    """Publishes the demo ruleset and policy and runs every scenario; returns scenario -> TR number
    and the reference lookup the scenarios used. The caller commits."""
    publish_ruleset(session, Ruleset.model_validate_json(ruleset_text("demo_metals")), SUE)
    publish_policy(session, DEMO_POLICY, SUE)
    lookup, samples, specs = demo_reference()
    for days_ago, (tr, run) in zip(range(len(SCENARIOS), 0, -1), SCENARIOS.values(), strict=True):
        run(_Scenario(session, lookup, samples, today - timedelta(days=days_ago), tr, f"PB-{tr[2:]}", specs))
    return {name: tr for name, (tr, _) in SCENARIOS.items()}, lookup
