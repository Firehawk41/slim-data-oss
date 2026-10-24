"""Read models (DESIGN.md §11): what the report engine renders and what the review screens show.

Every read takes `as_of` (D-21), so a report can be rebuilt exactly as it stood when it was issued.

* `report_view(tr)`: per result, the latest **released** version only, with the text the report
  prints, ½Δ where it bears on a spec (D-80), recovery, the standard qualifier of an acceptance or
  exception (D-11), and the INFO flags the report shows (spec exceedances, D-60). Unreleased results
  are listed as waiting.
* `batch_view(batch)`: every result of a prep batch as it stands, for review, with what the engine
  saw and chose for it (its cups, D-81).
* `batches()`: the prep batches, newest first, for the host's list of batches.
"""

from datetime import datetime
from decimal import ROUND_CEILING, Decimal

from sqlalchemy.orm import Session

from slim_data.domain.base import Dec, Frozen
from slim_data.domain.enums import EventKind, Qualifier, ResultStatus, Severity, Verdict
from slim_data.domain.results import Result, ResultVersion
from slim_data.domain.values import AnalyteRef, Quantity, SampleRef
from slim_data.persistence._rows import NotFound
from slim_data.persistence.observations import get_batch, list_batches
from slim_data.persistence.results import load_result, results_for_batch, results_for_tr


def value_text(version: ResultVersion) -> str:
    """How the value reads on the report (D-54): `12.400`, `< 0.050`, `0.120 (estimated)`."""
    if version.reported is None:
        return "N/A"
    number = str(version.reported.value)
    if version.qualifier == Qualifier.BELOW_MDL:
        return f"< {number}"
    if version.qualifier == Qualifier.ESTIMATED:
        return f"{number} (estimated)"
    return number


class ReportLineView(Frozen):
    sample: SampleRef
    analyte: AnalyteRef
    version: int
    text: str
    reported: Quantity | None
    qualifier: Qualifier
    uncertainty: Dec | None  # ½Δ (D-55): always kept; the report prints it only as `half_delta`
    half_delta: Dec | None = None  # ½Δ when it matters to a spec limit (D-80); else None, not printed
    recovery: Dec | None  # beside the result, never applied (D-38)
    mdl: Dec | None = None  # effective MDL, rounded up to the result's places (D-79, D-64); None: no MDL
    ql: Dec | None = None  # quantitation limit, rounded the same way
    notes: tuple[str, ...] = ()  # standard qualifiers: acceptance / exception categories (D-11)
    shown_flags: tuple[str, ...] = ()  # INFO flags the report shows, e.g. ABOVE_SPEC (D-60)
    released_with_exception: bool = False
    solo_signoff: bool = False  # internal record (D-68), not printed for the customer
    amended: bool = False  # a correction of a result reported before (D-20)
    released_at: datetime | None = None


class ReportView(Frozen):
    tr_number: str
    lines: tuple[ReportLineView, ...]
    waiting: tuple[str, ...] = ()  # "TR00042-03 Fe": results not released yet

    @property
    def complete(self) -> bool:
        return bool(self.lines) and not self.waiting


def report_view(session: Session, tr_number: str, as_of: datetime | None = None) -> ReportView:
    lines, waiting = [], []
    for result in results_for_tr(session, tr_number, as_of):
        line = _released_line(result)
        if line is None:
            if result.status != ResultStatus.REJECTED:
                waiting.append(f"{result.sample} {result.analyte.code}")
        else:
            lines.append(line)
    return ReportView(tr_number=tr_number, lines=tuple(lines), waiting=tuple(waiting))


def _released_line(result: Result) -> ReportLineView | None:
    released = [e for e in result.events if e.kind == EventKind.RELEASED]
    if not released:
        return None
    event = released[-1]
    version = result.versions[event.version - 1]
    events = result.events_for(version.version)
    notes = tuple(e.reason_category for e in events if e.kind in (EventKind.ACCEPTED, EventKind.EXCEPTION))
    return ReportLineView(
        sample=result.sample,
        analyte=result.analyte,
        version=version.version,
        text=value_text(version),
        reported=version.reported,
        qualifier=version.qualifier,
        uncertainty=version.uncertainty,
        half_delta=_half_delta_shown(version),
        recovery=version.recovery,
        mdl=_limit(version, "effective_mdl"),
        ql=_limit(version, "ql"),
        notes=notes,
        shown_flags=tuple(
            f.code for f in version.flags if f.severity == Severity.INFO and f.step.startswith("spec_")
        ),
        released_with_exception=any(e.kind == EventKind.EXCEPTION for e in events),
        solo_signoff=any(e.solo for e in events),
        amended=released[0].version < version.version,
        released_at=event.at,
    )


def _step(version: ResultVersion, name: str) -> dict | None:
    """The trace entry of a step (any version of it), or None if it didn't run on this result."""
    steps = version.trace.get("steps", ())
    return next((s for s in steps if s.get("step", "").split("@")[0] == name), None)


def _limit(version: ResultVersion, key: str) -> Decimal | None:
    """A detection limit from the reported_value step's trace, rounded *up* to the result's decimal
    places, like the "<" figure (D-64): a report never states a limit lower than the one achieved."""
    step = _step(version, "reported_value")
    if step is None or step.get(key) is None:
        return None
    return Decimal(step[key]).quantize(Decimal(1).scaleb(-step["places"]), rounding=ROUND_CEILING)


def _half_delta_shown(version: ResultVersion) -> Decimal | None:
    """½Δ goes on the report only where it bears on a spec limit (D-80): the result is outside the
    limit, or within ½Δ of it. Compared unrounded (D-54); printed rounded like the value."""
    spec, half = _step(version, "spec_limits"), _step(version, "uncertainty")
    if not (spec and spec.get("compared") and half and half.get("half_delta")):
        return None
    value, reach = Decimal(spec["value"]), Decimal(half["half_delta"])
    high = spec.get("high") is not None and value > Decimal(spec["high"]) - reach
    low = spec.get("low") is not None and value < Decimal(spec["low"]) + reach
    return version.uncertainty if high or low else None


class FlagView(Frozen):
    code: str
    severity: Severity
    message: str


class CupsView(Frozen):
    """What the engine saw and chose for one result, read from its trace (D-81)."""

    cups: tuple[str, ...] = ()  # dilution-corrected, as the agreement step compared them
    agree: bool | None = None
    chosen: int | None = None  # the cup reported when they disagreed; None: their mean, or nothing
    why: str = ""  # the cup_choice rung that decided (D-31): scale_fault, spike_witness, ...
    recovery: Dec | None = None
    recovery_basis: str = ""  # recovery_pooling's basis: own, batch_median, none (D-38)
    blank: str | None = None
    mdl: Dec | None = None
    ql: Dec | None = None
    half_delta: Dec | None = None  # staff always see it; the report only near a limit (D-80)


def cups_view(version: ResultVersion) -> CupsView:
    agreement, choice = _step(version, "agreement") or {}, _step(version, "cup_choice") or {}
    pooling, blank = _step(version, "recovery_pooling") or {}, _step(version, "method_blank") or {}
    return CupsView(
        cups=tuple(agreement[k] for k in ("cup_1", "cup_2") if agreement.get(k) is not None),
        agree=agreement.get("agree"),
        chosen=version.trace.get("chosen_replicate"),
        why=choice.get("rung", ""),
        recovery=version.recovery,
        recovery_basis=pooling.get("basis", ""),
        blank=blank.get("blank"),
        mdl=_limit(version, "effective_mdl"),
        ql=_limit(version, "ql"),
        half_delta=version.uncertainty,
    )


class BatchResultView(Frozen):
    sample: SampleRef
    analyte: AnalyteRef
    version: int
    status: ResultStatus
    verdict: Verdict
    text: str
    flags: tuple[FlagView, ...]
    rerun_requested: bool
    correction_started: bool
    analyst_ids: frozenset[str]
    cups: CupsView = CupsView()


class BatchView(Frozen):
    code: str
    prep_method: str
    samples: tuple[SampleRef, ...]
    results: tuple[BatchResultView, ...]


def batch_view(session: Session, code: str, as_of: datetime | None = None) -> BatchView:
    batch = get_batch(session, code, as_of)
    rows = []
    for result in results_for_batch(session, batch.id, as_of):
        current = result.current
        rows.append(
            BatchResultView(
                sample=result.sample,
                analyte=result.analyte,
                version=current.version,
                status=result.status,
                verdict=current.verdict,
                text=value_text(current),
                flags=tuple(
                    FlagView(code=f.code, severity=f.severity, message=f.message) for f in current.flags
                ),
                rerun_requested=result.rerun_requested,
                correction_started=result.correction_started,
                analyst_ids=current.analyst_ids,
                cups=cups_view(current),
            )
        )
    return BatchView(
        code=batch.code, prep_method=batch.prep_method, samples=batch.samples, results=tuple(rows)
    )


class BatchSummary(Frozen):
    code: str
    analysis_id: int
    prep_method: str
    registered_at: datetime


def batches(session: Session, as_of: datetime | None = None) -> tuple[BatchSummary, ...]:
    return tuple(
        BatchSummary(
            code=r.code, analysis_id=r.analysis_id, prep_method=r.prep_method, registered_at=r.created_at
        )
        for r in list_batches(session, as_of)
    )


def trace(
    session: Session,
    sample: SampleRef,
    analyte: AnalyteRef,
    version: int | None = None,
    as_of: datetime | None = None,
) -> dict:
    """The decision trace of one version (D-16): why the result is what it is. Latest by default."""
    result = load_result(session, sample, analyte, as_of)
    if result is None or (version is not None and not 1 <= version <= len(result.versions)):
        raise NotFound(f"no such version of {sample} {analyte.code}")
    return result.versions[(version or len(result.versions)) - 1].trace
