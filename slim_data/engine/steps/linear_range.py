"""linear_range@1 (D-59, D-61): an instrument reading above the verified linear range is never
reported; above the top calibration standard but within the linear range is only noted.

Compared on the raw instrument reading (before dilution), which is what the range was verified on.
"""

from decimal import Decimal
from typing import Self

from pydantic import Field, model_validator

from slim_data.domain.base import Dec, Frozen
from slim_data.engine.model import BatchState
from slim_data.engine.numbers import text
from slim_data.engine.step import Step


class LinearRange(Frozen):
    code: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    linear_limit: Dec
    top_standard: Dec | None = None

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.linear_limit <= 0:
            raise ValueError("linear limit must be positive")
        if self.top_standard is not None and not 0 < self.top_standard <= self.linear_limit:
            raise ValueError("top standard must be positive and not above the linear limit")
        return self


class LinearRangeParams(Frozen):
    ranges: tuple[LinearRange, ...] = ()

    @model_validator(mode="after")
    def _unique(self) -> Self:
        keys = [(r.code, r.unit) for r in self.ranges]
        if len(set(keys)) != len(keys):
            raise ValueError("two linear ranges for the same analyte and unit")
        return self

    def find(self, code: str, unit: str) -> LinearRange | None:
        return next((r for r in self.ranges if r.code == code and r.unit == unit), None)


class LinearRangeCheck(Step):
    name = "linear_range"
    version = 1
    Params = LinearRangeParams
    flag_codes = frozenset({"ABOVE_LINEAR_RANGE", "ABOVE_TOP_STANDARD"})

    def run(self, state: BatchState, params: LinearRangeParams) -> None:
        for cell in state.cells.values():
            readings = [*cell.cups.values(), *cell.spikes.values()]
            if not readings:
                continue
            limits = params.find(cell.analyte.code, readings[0].value.unit)
            if limits is None:
                continue
            highest: Decimal = max(r.value.value for r in readings)
            if highest > limits.linear_limit:
                self.flag(
                    cell,
                    "ABOVE_LINEAR_RANGE",
                    f"{highest} > linear limit {limits.linear_limit}: dilute and rerun",
                )
            elif limits.top_standard is not None and highest > limits.top_standard:
                self.flag(cell, "ABOVE_TOP_STANDARD", f"{highest} > top standard {limits.top_standard}")
            self.trace(
                cell,
                highest_reading=text(highest),
                linear_limit=text(limits.linear_limit),
                top_standard=text(limits.top_standard),
            )
