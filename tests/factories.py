"""Builders for domain objects in tests: sensible defaults, override what the test is about."""

from datetime import UTC, datetime
from decimal import Decimal

from slim_data.domain.actor import Actor
from slim_data.domain.enums import IdentitySource, Qualifier, Role, Severity
from slim_data.domain.results import Flag, Result, ResultEvent, ResultVersion
from slim_data.domain.values import AnalyteRef, Quantity, SampleRef

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def actor(id: str = "ana", *roles: Role, source: IdentitySource = IdentitySource.AUTHENTICATED) -> Actor:
    return Actor(id=id, display=id.title(), roles=frozenset(roles or (Role.ANALYST,)), identity_source=source)


ANA = actor("ana", Role.ANALYST)
ROB = actor("rob", Role.REVIEWER)
SUE = actor("sue", Role.SUPERVISOR)


def sample(position: int = 1, tr: str = "TR00042") -> SampleRef:
    return SampleRef(tr_number=tr, position=position)


def analyte(code: str = "Fe", analysis_id: int = 1) -> AnalyteRef:
    return AnalyteRef(analysis_id=analysis_id, code=code)


def qty(value: str, unit: str = "ppb") -> Quantity:
    return Quantity(value=Decimal(value), unit=unit)


def flag(severity: Severity, code: str = "TEST_FLAG") -> Flag:
    return Flag(code=code, severity=severity, step="test@1")


def version(n: int = 1, *flags: Flag, analysts: tuple[str, ...] = ("ana",), **overrides) -> ResultVersion:
    fields = dict(
        version=n,
        ruleset_id=1,
        reported=qty("12.400"),
        qualifier=Qualifier.DETECTED,
        uncertainty=Decimal("0.200"),
        flags=flags,
        analyst_ids=frozenset(analysts),
        reason="" if n == 1 else "re-run in RUN-0043",
        created_by=ANA,
    )
    fields.update(overrides)
    return ResultVersion(**fields)


def result(*versions: ResultVersion, events: tuple[ResultEvent, ...] = ()) -> Result:
    return Result(sample=sample(), analyte=analyte(), versions=versions or (version(),), events=events)
