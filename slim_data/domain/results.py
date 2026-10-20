"""What was decided: flags, result versions, events and the Result aggregate (DESIGN.md §6.2).

A result version is immutable. Status is never a column: it is read from the events, so who
reviewed, accepted or released what, and when, is never lost.
"""

from typing import Any, Self

from pydantic import AwareDatetime, Field, model_validator

from slim_data.domain.actor import Actor
from slim_data.domain.base import Dec, Frozen
from slim_data.domain.enums import EventKind, Qualifier, ResultStatus, Severity, Verdict
from slim_data.domain.values import AnalyteRef, Quantity, SampleRef

_VERDICT_FOR_SEVERITY = {
    Severity.INFO: Verdict.REPORTABLE,
    Severity.WARN: Verdict.REPORTABLE,
    Severity.NEEDS_SUPERVISOR: Verdict.NEEDS_SUPERVISOR,
    Severity.RERUN: Verdict.RERUN,
}


class Flag(Frozen):
    """One named finding from an engine step (D-56)."""

    code: str = Field(min_length=1, max_length=60)  # e.g. "CUP_CHOSEN_BY_SPIKE"
    severity: Severity
    step: str = Field(min_length=1, max_length=60)  # e.g. "cup_choice@1"
    message: str = ""


def verdict_from_flags(flags: tuple[Flag, ...]) -> Verdict:
    """The worst flag decides (D-56); no flags, or only INFO/WARN, is REPORTABLE."""
    if not flags:
        return Verdict.REPORTABLE
    return _VERDICT_FOR_SEVERITY[max(flag.severity for flag in flags)]


class ResultVersion(Frozen):
    """One immutable version of one result (sample × analyte)."""

    version: int = Field(ge=1)
    ruleset_id: int = Field(ge=1)  # the published ruleset that produced it (D-12)
    raw: Dec | None = None  # unrounded, dilution-corrected value, kept beside the reported one (D-54)
    reported: Quantity | None  # rounded value; for BELOW_MDL, the effective MDL
    qualifier: Qualifier
    uncertainty: Dec | None = None  # ± half-delta, same unit (D-55); None reads N/A
    recovery: Dec | None = None  # pooled spike recovery, a fraction, shown beside the result (D-38)
    flags: tuple[Flag, ...] = ()
    trace: dict[str, Any] = Field(default_factory=dict)  # step-by-step decision trace (D-16)
    limits: dict[str, Any] = Field(default_factory=dict)  # spec limits as they stood (D-04)
    analyst_ids: frozenset[str] = Field(min_length=1)  # who entered the readings (four-eyes)
    reason: str = ""  # why this version exists; required from version 2
    created_by: Actor
    created_at: AwareDatetime | None = None  # database clock (D-21)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.version > 1 and not self.reason.strip():
            raise ValueError("a new version needs a reason")
        if self.qualifier == Qualifier.BELOW_MDL and self.uncertainty is not None:
            raise ValueError("a result below MDL has no ± (D-55)")
        if self.uncertainty is not None and self.uncertainty < 0:
            raise ValueError("± cannot be negative")
        return self

    @property
    def verdict(self) -> Verdict:
        return verdict_from_flags(self.flags)


_NEEDS_CATEGORY = {EventKind.ACCEPTED, EventKind.EXCEPTION}
_NEEDS_NOTE = {EventKind.EXCEPTION, EventKind.REJECTED, EventKind.CORRECTION_STARTED}
_STATUS_FOR_KIND = {
    EventKind.REVIEWED: ResultStatus.REVIEWED,
    EventKind.RELEASED: ResultStatus.RELEASED,
    EventKind.REJECTED: ResultStatus.REJECTED,
}


class ResultEvent(Frozen):
    """Something a person did to one version of a result."""

    kind: EventKind
    version: int = Field(ge=1)
    actor: Actor
    reason_category: str = ""  # standard category, printed as qualifier text on the report (D-11)
    note: str = ""  # free text; stays internal (D-11)
    at: AwareDatetime | None = None  # database clock (D-21)

    @model_validator(mode="after")
    def _required_fields(self) -> Self:
        if self.kind in _NEEDS_CATEGORY and not self.reason_category.strip():
            raise ValueError(f"{self.kind.name} needs a reason category")
        if self.kind in _NEEDS_NOTE and not self.note.strip():
            raise ValueError(f"{self.kind.name} needs a note")
        return self


class Result(Frozen):
    """The aggregate callers get back: every version and every event of one result."""

    id: int | None = None
    sample: SampleRef
    analyte: AnalyteRef
    versions: tuple[ResultVersion, ...] = Field(min_length=1)
    events: tuple[ResultEvent, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if [v.version for v in self.versions] != list(range(1, len(self.versions) + 1)):
            raise ValueError("versions must be numbered 1..n in order")
        if any(event.version > len(self.versions) for event in self.events):
            raise ValueError("an event refers to a version that doesn't exist")
        return self

    @property
    def current(self) -> ResultVersion:
        return self.versions[-1]

    def events_for(self, version: int) -> tuple[ResultEvent, ...]:
        return tuple(event for event in self.events if event.version == version)

    def _current_kinds(self) -> set[EventKind]:
        return {event.kind for event in self.events_for(self.current.version)}

    @property
    def status(self) -> ResultStatus:
        """Status of the current version: the latest status-changing event, else DRAFT."""
        status = ResultStatus.DRAFT
        for event in self.events_for(self.current.version):
            status = _STATUS_FOR_KIND.get(event.kind, status)
        return status

    @property
    def rerun_requested(self) -> bool:
        return EventKind.RERUN_REQUESTED in self._current_kinds()

    @property
    def correction_started(self) -> bool:
        return EventKind.CORRECTION_STARTED in self._current_kinds()

    @property
    def is_cleared(self) -> bool:
        """Whether the current verdict has been dealt with (D-57): REPORTABLE needs nothing,
        NEEDS_SUPERVISOR needs an acceptance, RERUN needs an exception."""
        verdict = self.current.verdict
        if verdict == Verdict.REPORTABLE:
            return True
        needed = EventKind.ACCEPTED if verdict == Verdict.NEEDS_SUPERVISOR else EventKind.EXCEPTION
        return needed in self._current_kinds()

    @property
    def released_with_exception(self) -> bool:
        """Permanent (D-09): true once any version was released under an exception."""
        return any(
            {EventKind.EXCEPTION, EventKind.RELEASED} <= {e.kind for e in self.events_for(v.version)}
            for v in self.versions
        )

    @property
    def is_amended(self) -> bool:
        """A version after one that was released exists (D-20): the next report is "Amended"."""
        released = [e.version for e in self.events if e.kind == EventKind.RELEASED]
        return bool(released) and self.current.version > min(released)
