"""method_blank@1 (D-58, D-64): the 10× blank rule. Blanks are never subtracted.

A blank belongs to the prep batch, not to a sample (D-64). Per element, the highest blank (cautious,
when there are several) is compared with its effective MDL (the cell's, if the blank has none):
  - at or below MDL → fine
  - above MDL: a sample ≥ 10× the blank → BLANK_DETECTED; below 10× → BLANK_SIGNIFICANT (the
    contamination could be a large share of the result). A result below MDL is unaffected: a
    contaminated blank can only have pushed it up.
No blank in the batch's unit → BLANK_MISSING.
"""

from decimal import Decimal

from pydantic import Field

from slim_data.domain.base import Dec, Frozen
from slim_data.domain.enums import Qualifier
from slim_data.engine.model import BatchState
from slim_data.engine.numbers import text
from slim_data.engine.step import Step


class MethodBlankParams(Frozen):
    multiple: Dec = Field(default=Decimal(10), gt=1)


class MethodBlank(Step):
    name = "method_blank"
    version = 1
    Params = MethodBlankParams
    requires = ("reported_value",)
    flag_codes = frozenset({"BLANK_MISSING", "BLANK_DETECTED", "BLANK_SIGNIFICANT"})

    def run(self, state: BatchState, params: MethodBlankParams) -> None:
        for cell in state.cells.values():
            value = cell.value()
            if value is None:
                continue
            blanks = [b for b in state.blanks if b.analyte == cell.analyte and b.value.unit == cell.unit]
            if not blanks:
                self.flag(cell, "BLANK_MISSING", "no method blank for this element in the batch")
                self.trace(cell, blank=None)
                continue
            highest = max(blanks, key=lambda b: (b.value.value * b.dilution, b.replicate))
            blank = highest.value.value * highest.dilution
            mdl = highest.effective_mdl.value if highest.effective_mdl else cell.mdl
            detail = {"blank": text(blank), "blank_mdl": text(mdl), "blanks": len(blanks)}
            if mdl is None or blank <= mdl:
                self.trace(cell, contaminated=False if mdl is not None else None, **detail)
                continue
            if cell.qualifier == Qualifier.BELOW_MDL:
                self.trace(cell, contaminated=True, affects="no: result below MDL", **detail)
            elif value >= params.multiple * blank:
                self.flag(cell, "BLANK_DETECTED", f"blank {blank} > MDL; result ≥ {params.multiple}× blank")
                self.trace(cell, contaminated=True, affects="minor", **detail)
            else:
                self.flag(
                    cell, "BLANK_SIGNIFICANT", f"blank {blank} > MDL; result < {params.multiple}× blank"
                )
                self.trace(cell, contaminated=True, affects="significant", **detail)
