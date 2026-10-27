"""reported_value@1 (D-54) and @2 (D-94): the number the report shows.

The value is the mean of agreeing cups, else the chosen cup, already × dilution. Below the effective
MDL it reads "< MDL"; between MDL and the optional quantitation limit (QL) it is "estimated". It is
rounded once, here, to the analyte's decimal places, half to even. Every earlier comparison used the
unrounded value, and the unrounded value is kept beside the rounded one.

The "<" figure is the effective MDL rounded *up*, so the report never states a limit lower than the
one actually achieved (D-64).

@1 compares against the larger effective MDL of the two cups. @2 compares against the MDL of the cups
the result kept (D-94): the mean of both cups' effective MDLs when they agree (MDL × the average
dilution), the chosen cup's own when one was chosen. Agreement and cup choice still used the larger,
before anything was kept; the later steps (blank, spec limits) see the kept MDL.
"""

from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal
from typing import Self

from pydantic import Field, model_validator

from slim_data.domain.base import Dec, Frozen
from slim_data.domain.enums import Qualifier
from slim_data.engine.model import BatchState, Selection
from slim_data.engine.numbers import text
from slim_data.engine.step import Step


class Places(Frozen):
    code: str = Field(min_length=1)
    places: int = Field(ge=0, le=10)


class ReportedValueParams(Frozen):
    default_places: int = Field(default=3, ge=0, le=10)
    places: tuple[Places, ...] = ()
    ql_multiple: Dec | None = Field(default=Decimal("3.33"), gt=1)  # QL = this × MDL; None = no QL

    @model_validator(mode="after")
    def _unique(self) -> Self:
        codes = [p.code for p in self.places]
        if len(set(codes)) != len(codes):
            raise ValueError("two decimal-place rows for the same analyte")
        return self

    def places_for(self, code: str) -> int:
        return next((p.places for p in self.places if p.code == code), self.default_places)


def round_to(value: Decimal, places: int, rounding: str = ROUND_HALF_EVEN) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=rounding)


class ReportedValue(Step):
    name = "reported_value"
    version = 1
    Params = ReportedValueParams
    requires = ("cup_shape", "agreement", "cup_choice")

    def run(self, state: BatchState, params: ReportedValueParams) -> None:
        state.shared["reported_value"] = params
        for cell in state.cells.values():
            value = cell.value()
            if value is None:
                continue
            kept = self._keep_mdl(cell)
            places = params.places_for(cell.analyte.code)
            ql = None if cell.mdl is None or params.ql_multiple is None else params.ql_multiple * cell.mdl
            if cell.mdl is not None and value < cell.mdl:
                cell.qualifier = Qualifier.BELOW_MDL
                cell.reported = round_to(cell.mdl, places, ROUND_CEILING)
            else:
                cell.qualifier = Qualifier.ESTIMATED if ql is not None and value < ql else Qualifier.DETECTED
                cell.reported = round_to(value, places)
            self.trace(
                cell,
                raw=text(value),
                **kept,
                effective_mdl=text(cell.mdl),
                ql=text(ql),
                places=places,
                qualifier=cell.qualifier.name,
                reported=text(cell.reported),
            )

    def _keep_mdl(self, cell) -> dict:
        """@1 keeps the larger of the cups' effective MDLs, set by cup_shape. Returns trace fields."""
        return {}


class ReportedValue2(ReportedValue):
    version = 2

    def _keep_mdl(self, cell) -> dict:
        if cell.mdl is None:  # a cup without an MDL: cup_shape flagged it
            return {"mdl_from": None}
        if cell.selection == Selection.AGREED:
            cell.mdl = sum(r.effective_mdl.value for r in cell.cups.values()) / len(cell.cups)
            return {"mdl_from": "both cups"}
        cell.mdl = cell.cups[cell.chosen_replicate].effective_mdl.value
        return {"mdl_from": f"cup {cell.chosen_replicate}"}
