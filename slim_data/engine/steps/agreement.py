"""agreement@1 (D-28 to D-31, D-34): do two replicates agree?

Three zones, chosen by the smaller value: both below MDL → agree; smaller below the switch point →
absolute rule |a − b| ≤ absolute limit; otherwise the RPD rule |a − b| / mean ≤ RPD limit. The same
function checks spike against spike (D-34).
"""

from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from slim_data.domain.base import Dec, Frozen
from slim_data.engine.model import BatchState, Selection
from slim_data.engine.numbers import rpd, text
from slim_data.engine.step import Step


class AgreementParams(Frozen):
    switch_multiple: Dec = Field(default=Decimal(5), gt=0)  # switch point = this × MDL
    rpd_limit: Dec = Field(default=Decimal("0.20"), gt=0)
    absolute_multiple: Dec = Field(default=Decimal(1), gt=0)  # absolute limit = this × MDL


class Zone(StrEnum):
    BELOW_MDL = "below_mdl"
    ABSOLUTE = "absolute"
    RPD = "rpd"


def replicates_agree(
    a: Decimal, b: Decimal, mdl: Decimal | None, params: AgreementParams
) -> tuple[bool, Zone]:
    smaller = min(a, b)
    if mdl is not None:
        if a < mdl and b < mdl:
            return True, Zone.BELOW_MDL
        if smaller < params.switch_multiple * mdl:
            return abs(a - b) <= params.absolute_multiple * mdl, Zone.ABSOLUTE
    return rpd(a, b) <= params.rpd_limit, Zone.RPD


class Agreement(Step):
    name = "agreement"
    version = 1
    Params = AgreementParams
    flag_codes = frozenset({"CUPS_DISAGREE"})
    requires = ("cup_shape",)

    def warnings(self, params: AgreementParams) -> list[str]:
        if params.absolute_multiple != params.rpd_limit * params.switch_multiple:
            return [
                f"agreement: absolute limit {params.absolute_multiple}× MDL ≠ RPD {params.rpd_limit} × "
                f"switch {params.switch_multiple}× MDL, so the limit jumps at the switch point (D-30)"
            ]
        return []

    def run(self, state: BatchState, params: AgreementParams) -> None:
        state.shared["agreement"] = params
        for cell in state.cells.values():
            if not cell.usable:
                continue
            a, b = cell.cup_value(1), cell.cup_value(2)
            agree, zone = replicates_agree(a, b, cell.mdl, params)
            cell.agree = agree
            if agree:
                cell.selection = Selection.AGREED
            else:
                self.flag(cell, "CUPS_DISAGREE", f"cups {a} and {b} ({zone.value} rule)")
            self.trace(cell, cup_1=text(a), cup_2=text(b), zone=zone.value, agree=agree)
