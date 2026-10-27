"""precision@1 (D-88, D-90): suggestions from the instrument's own evidence on each cup.

  - RSD_HIGH: the replicate RSD above the ruleset's limit. Judged only when the cup's value is at
    least `rsd_from_mdl_multiple` × its effective MDL (MDL × dilution, D-54): near the MDL counts are
    low and a high RSD is normal. No MDL, no zone: not judged.
  - ISTD_LOW / ISTD_HIGH: the internal standard's recovery outside the window (matrix suppression,
    drift).
  - RSD_MISSING / ISTD_MISSING: no such evidence, only when the ruleset requires it.
A cup the analyst excluded isn't judged. These are suggestions: the ruleset rates them, the demo WARN,
and the analyst decides (D-88). The trace keeps each cup's RSD and recovery: what a chart plots.
"""

from decimal import Decimal
from typing import Self

from pydantic import Field, model_validator

from slim_data.domain.base import Dec, Frozen
from slim_data.engine.model import BatchState
from slim_data.engine.numbers import text
from slim_data.engine.step import Step


class PrecisionParams(Frozen):
    rsd_limit: Dec | None = Field(default=None, gt=0)  # %, e.g. 10; None = RSD not judged
    rsd_from_mdl_multiple: Dec = Field(default=Decimal(10), ge=0)
    istd_low: Dec | None = Field(default=None, gt=0)  # %, e.g. 50
    istd_high: Dec | None = Field(default=None, gt=0)  # %, e.g. 150
    rsd_required: bool = False
    istd_required: bool = False

    @model_validator(mode="after")
    def _window(self) -> Self:
        if (self.istd_low is None) != (self.istd_high is None):
            raise ValueError("an internal standard window needs both ends")
        if self.istd_low is not None and self.istd_low >= self.istd_high:
            raise ValueError("the internal standard window's low end must be below its high end")
        return self


class Precision(Step):
    name = "precision"
    version = 1
    Params = PrecisionParams
    flag_codes = frozenset({"RSD_HIGH", "RSD_MISSING", "ISTD_LOW", "ISTD_HIGH", "ISTD_MISSING"})
    requires = ("cup_shape",)

    def run(self, state: BatchState, params: PrecisionParams) -> None:
        for cell in state.cells.values():
            found: dict[str, list[str]] = {}
            cups = []
            for replicate, reading in sorted(cell.cups.items()):
                if replicate in cell.excluded:
                    continue
                value = cell.cup_value(replicate)
                floor = (
                    None
                    if reading.mdl is None
                    else params.rsd_from_mdl_multiple * reading.effective_mdl.value
                )
                judged = params.rsd_limit is not None and reading.rsd is not None and floor is not None
                judged = judged and abs(value) >= floor
                cups.append(
                    {
                        "replicate": replicate,
                        "rsd": None if reading.rsd is None else text(reading.rsd),
                        "rsd_judged": judged,
                        "rsd_floor": None if floor is None else text(floor),
                        "istd_recovery": None
                        if reading.istd_recovery is None
                        else text(reading.istd_recovery),
                    }
                )
                if reading.rsd is None and params.rsd_required:
                    found.setdefault("RSD_MISSING", []).append(f"cup {replicate}")
                if judged and reading.rsd > params.rsd_limit:
                    found.setdefault("RSD_HIGH", []).append(f"cup {replicate} {_pct(reading.rsd)}")
                recovery = reading.istd_recovery
                if recovery is None:
                    if params.istd_required:
                        found.setdefault("ISTD_MISSING", []).append(f"cup {replicate}")
                elif params.istd_low is not None and recovery < params.istd_low:
                    found.setdefault("ISTD_LOW", []).append(f"cup {replicate} {_pct(recovery)}")
                elif params.istd_high is not None and recovery > params.istd_high:
                    found.setdefault("ISTD_HIGH", []).append(f"cup {replicate} {_pct(recovery)}")
            for code, where in sorted(found.items()):
                self.flag(cell, code, f"{', '.join(where)}{_limit(code, params)}")
            self.trace(cell, cups=cups)


def _pct(value: Decimal) -> str:
    return f"{text(value)} %"


def _limit(code: str, params: PrecisionParams) -> str:
    if code == "RSD_HIGH":
        return f" (limit {_pct(params.rsd_limit)} above {text(params.rsd_from_mdl_multiple)} × MDL)"
    if code in ("ISTD_LOW", "ISTD_HIGH"):
        return f" (window {_pct(params.istd_low)}–{_pct(params.istd_high)})"
    return ": no figure in the file"
