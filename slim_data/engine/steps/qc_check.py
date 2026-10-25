"""qc_check@1 (D-87): check standards and CRMs, judged against their expected values.

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
from typing import Self

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
