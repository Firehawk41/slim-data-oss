"""`verify` (DESIGN.md §7.3, §11; D-17, D-76): proves the stored results are what the rules made.

* **Replay:** every stored result version is re-run through the ruleset version it names, on the
  batch's readings and exclusions as they stood when the version was made (`as_of`), with the spec
  limits it snapshotted. The engine's output must match what was stored, field for field.
* **Audit chain:** slim-domain's hash-chained `audit_log` belongs to the host's database, not to
  slim-data, so the host passes its checker in (`chain_check`). Without one (SQLite, or no audit
  log installed) the report says the chain was not checked, never that it is intact.

Read-only: nothing is written.
"""

from collections.abc import Callable, Sequence

from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from slim_data.domain.base import Frozen
from slim_data.domain.results import ResultVersion
from slim_data.domain.values import AnalyteRef, Limits, SampleRef
from slim_data.engine.model import EvaluationInput, SampleLimits
from slim_data.engine.registry import StepRegistry
from slim_data.engine.ruleset import RulesetError
from slim_data.engine.runner import BatchRejected, evaluate
from slim_data.persistence._rows import NotFound
from slim_data.persistence.observations import evaluation_input
from slim_data.persistence.results import every_version
from slim_data.persistence.rulesets import PublishedRuleset, get_ruleset
from slim_data.service.workflow import _COMPARED, _version

# The host's audit-chain checker: given a connection, the IDs of entries that don't check out.
ChainCheck = Callable[[Connection], Sequence[int]]


class VerifyReport(Frozen):
    versions: int
    mismatches: tuple[str, ...] = ()
    chain_checked: bool = False
    broken_entries: tuple[int, ...] = ()

    @property
    def ok(self) -> bool:
        """Nothing wrong found. Read with `chain_checked`: an unchecked chain is not an intact one."""
        return not self.mismatches and not self.broken_entries


def verify(
    session: Session, chain_check: ChainCheck | None = None, registry: StepRegistry | None = None
) -> VerifyReport:
    stored = every_version(session)
    rulesets: dict[int, PublishedRuleset | str] = {}
    mismatches = []
    for sample, analyte, batch_code, version in stored:
        problem = _replay(session, sample, analyte, batch_code, version, rulesets, registry)
        if problem:
            mismatches.append(f"{sample} {analyte.code} v{version.version}: {problem}")
    broken = None if chain_check is None else tuple(chain_check(session.connection()))
    return VerifyReport(
        versions=len(stored),
        mismatches=tuple(mismatches),
        chain_checked=broken is not None,
        broken_entries=broken or (),
    )


def _replay(
    session,
    sample: SampleRef,
    analyte: AnalyteRef,
    batch_code: str,
    stored: ResultVersion,
    rulesets: dict,
    registry,
) -> str | None:
    """What differs between the stored version and a fresh evaluation; None when they match."""
    if stored.ruleset_id not in rulesets:
        try:
            rulesets[stored.ruleset_id] = get_ruleset(session, id=stored.ruleset_id, registry=registry)
        except (NotFound, RulesetError) as error:  # e.g. the ruleset body no longer matches its hash
            rulesets[stored.ruleset_id] = f"ruleset {stored.ruleset_id}: {error}"
    published = rulesets[stored.ruleset_id]
    if isinstance(published, str):
        return published
    then = evaluation_input(session, batch_code, as_of=stored.created_at)
    limits = Limits.model_validate(stored.limits) if stored.limits else None
    data = EvaluationInput(
        batch=then.batch,
        readings=then.readings,
        excluded_reading_ids=then.excluded_reading_ids,
        limits=() if limits is None else (SampleLimits(sample=sample, analyte=analyte, limits=limits),),
    )
    try:
        cells = evaluate(data, published.validated).cells
    except BatchRejected as error:
        return f"the batch no longer evaluates: {error}"
    cell = next((c for c in cells if (c.sample, c.analyte) == (sample, analyte)), None)
    if cell is None:
        return "a replay doesn't produce this result"
    fresh = _version(cell, published, stored.analyst_ids, stored.created_by)
    differ = [name for name in _COMPARED if getattr(fresh, name) != getattr(stored, name)]
    return f"differs from a replay in {', '.join(differ)}" if differ else None
