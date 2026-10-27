"""qc_check@1 (D-87) and @2 (D-97, below): check standards and CRMs, judged against their expected values.

A check standard (CCV) or CRM belongs to the prep batch, not to a sample. Per role and element, each
reading's recovery = measured (× dilution) ÷ expected, judged against the role's window:
  - any reading outside the window → CHECK_FAILED / CRM_FAILED on every result of that element in
    the batch (cautious, like the highest blank: one failed check is enough)
  - no reading, and the ruleset says the role is required → CHECK_MISSING / CRM_MISSING
A role the ruleset gives no window for is not judged. Batch-wide: readings carry no run order yet, so
a check doesn't bracket the samples run since the last good one (waits for slim-prep's run list).
The trace keeps measured, expected and recovery per reading: what a control chart plots.
"""

from decimal import Decimal
from typing import Literal, Self

from pydantic import Field, model_validator

from slim_data.domain.base import Dec, Frozen
from slim_data.domain.enums import ReadingRole
from slim_data.engine.model import BatchState
from slim_data.engine.numbers import text
from slim_data.engine.step import Step


class QcWindow(Frozen):
    low: Dec = Field(gt=0)  # recovery as a fraction, e.g. 0.90
    high: Dec = Field(gt=0)
    required: bool = False

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.low >= self.high:
            raise ValueError("the window's low end must be below its high end")
        return self


class QcCheckParams(Frozen):
    check: QcWindow | None = None
    crm: QcWindow | None = None


def _percent(fraction: Decimal) -> str:
    return f"{(fraction * 100).quantize(Decimal(1))} %"


class QcCheck(Step):
    name = "qc_check"
    version = 1
    Params = QcCheckParams
    flag_codes = frozenset({"CHECK_FAILED", "CHECK_MISSING", "CRM_FAILED", "CRM_MISSING"})

    def run(self, state: BatchState, params: QcCheckParams) -> None:
        windows = [(r, w) for r, w in ((ReadingRole.CHECK, params.check), (ReadingRole.CRM, params.crm)) if w]
        judged = {}  # (role, analyte) -> [(reading, recovery)], computed once per element
        for cell in state.cells.values():
            detail = {}
            for role, window in windows:
                key = (role, cell.analyte)
                if key not in judged:
                    judged[key] = [
                        (
                            q,
                            q.value.value
                            * q.dilution
                            / state.batch.qc_expected(role, q.analyte).expected.value,
                        )
                        for q in state.qc
                        if q.role == role and q.analyte == cell.analyte
                    ]
                found = judged[key]
                label = role.name.lower()
                if not found:
                    if window.required:
                        self.flag(
                            cell, f"{role.name}_MISSING", f"no {_names[role]} for this element in the batch"
                        )
                    detail[label] = None
                    continue
                expected = state.batch.qc_expected(role, cell.analyte)
                detail[label] = [
                    {
                        "replicate": q.replicate,
                        "measured": text(q.value.value * q.dilution),
                        "expected": text(expected.expected.value),
                        "recovery": text(recovery),
                        "within": window.low <= recovery <= window.high,
                        "material": expected.material,
                        "lot": expected.lot,
                    }
                    for q, recovery in found
                ]
                worst = [r for _, r in found if not window.low <= r <= window.high]
                if worst:
                    furthest = max(worst, key=lambda r: abs(r - 1))
                    self.flag(
                        cell,
                        f"{role.name}_FAILED",
                        f"{_names[role]} recovered {_percent(furthest)}, "
                        f"outside {_percent(window.low)}–{_percent(window.high)}",
                    )
            self.trace(cell, **detail)


_names = {ReadingRole.CHECK: "check standard", ReadingRole.CRM: "CRM"}


# --- qc_check@2 (D-97) ---------------------------------------------------------------------------------


class QcRule(QcWindow):
    """A role's rule (D-97): the window, how a check is judged, and whether it brackets the samples.

    `judge`: "each_element" (each element's recovery on its own; the default, the sounder science) or
    "average" (one check passes or fails on the mean recovery of its elements, and that verdict applies
    to every element). `bracket`: a sample's reading counts only if the check standards run just before
    and just after it, in the same run, both passed (CHECK only: a CRM is prepped like a sample)."""

    judge: Literal["each_element", "average"] = "each_element"
    bracket: bool = False


class QcCheckParams2(Frozen):
    check: QcRule | None = None
    crm: QcRule | None = None

    @model_validator(mode="after")
    def _crm_is_batch_wide(self) -> Self:
        if self.crm is not None and self.crm.bracket:
            raise ValueError("a CRM is prepped like a sample: it doesn't bracket the run")
        return self


class _Check(Frozen):
    """One check standard as judged: a reading (each element) or a whole injection (average)."""

    run: str
    sequence: int | None
    replicate: int
    recoveries: tuple[tuple[str, Dec], ...]  # element -> recovery
    passed: bool
    recovery: Dec  # the one judged: the element's, or the mean
    material: str | None = None  # what a control chart groups by (D-87)
    lot: str | None = None

    def covers(self, code: str, judge: str) -> bool:
        return judge == "average" or any(c == code for c, _ in self.recoveries)


class QcCheck2(Step):
    """qc_check@2 (D-97): @1, plus judging the average of the elements and bracketing by run order.

    Readings without a run order (imported before D-97) are judged batch-wide, as by @1. A reading
    outside a bracket (no check after it, or before it, in its run) is CHECK_NOT_BRACKETED."""

    name = "qc_check"
    version = 2
    Params = QcCheckParams2
    flag_codes = QcCheck.flag_codes | {"CHECK_NOT_BRACKETED"}

    def run(self, state: BatchState, params: QcCheckParams2) -> None:
        rules = [(r, w) for r, w in ((ReadingRole.CHECK, params.check), (ReadingRole.CRM, params.crm)) if w]
        checks = {role: _checks(state, role, rule) for role, rule in rules}
        for cell in state.cells.values():
            detail = {}
            for role, rule in rules:
                label, name = role.name.lower(), _names[role]
                mine = [c for c in checks[role] if c.covers(cell.analyte.code, rule.judge)]
                if not mine:
                    if rule.required:
                        self.flag(cell, f"{role.name}_MISSING", f"no {name} for this element in the batch")
                    detail[label] = None
                    continue
                detail[label] = [
                    {"run": c.run, "sequence": c.sequence, "replicate": c.replicate,
                     "recovery": text(c.recovery), "within": c.passed,
                     "elements": {e: text(r) for e, r in c.recoveries}, "material": c.material, "lot": c.lot}
                    for c in mine
                ]  # fmt: skip
                readings = [*cell.cups.values(), *cell.spikes.values()]
                ordered = rule.bracket and all(r.sequence is not None for r in readings)
                ordered = ordered and all(c.sequence is not None for c in mine)
                if not ordered:  # batch-wide, as @1
                    failed = [c for c in mine if not c.passed]
                    if failed:
                        self._failed(cell, role, rule, max(failed, key=lambda c: abs(c.recovery - 1)))
                    continue
                brackets, failed, open_ = [], None, []
                for r in readings:
                    run = state.run_codes.get(r.run_id, str(r.run_id))
                    same = sorted((c for c in mine if c.run == run), key=lambda c: c.sequence)
                    before = next((c for c in reversed(same) if c.sequence < r.sequence), None)
                    after = next((c for c in same if c.sequence > r.sequence), None)
                    brackets.append(
                        {"reading": f"{r.role.name} {r.replicate}", "run": run, "sequence": r.sequence,
                         "before": before and before.sequence, "after": after and after.sequence}
                    )  # fmt: skip
                    if before is None or after is None:
                        open_.append(f"{r.role.name.lower()} {r.replicate}")
                    for c in (before, after):
                        if c is not None and not c.passed and failed is None:
                            failed = c
                detail[f"{label}_brackets"] = brackets
                if failed is not None:
                    self._failed(cell, role, rule, failed)
                if open_:
                    self.flag(
                        cell,
                        "CHECK_NOT_BRACKETED",
                        f"no check standard before and after {', '.join(open_)} in its run",
                    )
            self.trace(cell, **detail)

    def _failed(self, cell, role: ReadingRole, rule: QcRule, check: _Check) -> None:
        how = "averaged over its elements, " if rule.judge == "average" else ""
        where = f" (line {check.sequence} of run {check.run})" if check.sequence is not None else ""
        self.flag(
            cell,
            f"{role.name}_FAILED",
            f"{_names[role]}{where} recovered {how}{_percent(check.recovery)}, "
            f"outside {_percent(rule.low)}–{_percent(rule.high)}",
        )


def _checks(state: BatchState, role: ReadingRole, rule: QcRule) -> list[_Check]:
    """The role's checks as judged: one per reading (each element) or per injection (average): the
    readings of one line of one run, or of one replicate when there's no run order."""
    groups: dict = {}
    for q in state.qc:
        if q.role != role:
            continue
        run = state.run_codes.get(q.run_id, str(q.run_id))
        expected = state.batch.qc_expected(role, q.analyte)
        recovery = q.value.value * q.dilution / expected.expected.value
        if rule.judge == "each_element":
            key = (run, q.sequence, q.replicate, q.analyte.code)
        else:
            key = (run, q.sequence, None if q.sequence is not None else q.replicate)
        groups.setdefault(key, (run, q.sequence, q.replicate, expected, []))[4].append(
            (q.analyte.code, recovery)
        )
    out = []
    for run, sequence, replicate, expected, recoveries in groups.values():
        judged = sum(r for _, r in recoveries) / len(recoveries)  # one element: its own recovery
        out.append(
            _Check(
                run=run,
                sequence=sequence,
                replicate=replicate,
                recoveries=tuple(recoveries),
                passed=rule.low <= judged <= rule.high,
                recovery=judged,
                material=expected.material,
                lot=expected.lot,
            )
        )
    return out
