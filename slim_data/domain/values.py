"""Value objects: identities and quantities shared by every module (DESIGN.md §4)."""

import re
from typing import Self

from pydantic import Field, field_validator

from slim_data.domain.base import Dec, Frozen

TR_NUMBER_RE = re.compile(r"^TR\d{5,}$")
_SAMPLE_RE = re.compile(r"^(TR\d{5,})-(\d+)$")


class SampleRef(Frozen):
    """A TR sample by natural key: TR number + position, written `TR00042-03`."""

    tr_number: str
    position: int = Field(ge=1)

    @field_validator("tr_number")
    @classmethod
    def _tr_number_shape(cls, value: str) -> str:
        if not TR_NUMBER_RE.match(value):
            raise ValueError("TR numbers look like TR00042")
        return value

    @classmethod
    def parse(cls, text: str) -> Self:
        match = _SAMPLE_RE.match(text.strip())
        if not match:
            raise ValueError(f"not a lab sample ID (expected e.g. TR00042-03): {text!r}")
        return cls(tr_number=match.group(1), position=int(match.group(2)))

    def __str__(self) -> str:
        return f"{self.tr_number}-{self.position:02d}"


class AnalyteRef(Frozen):
    """What was measured (D-48): slim-domain's analysis ID + an element symbol or ion code."""

    analysis_id: int = Field(ge=1)
    code: str = Field(min_length=1, max_length=20)

    @field_validator("code")
    @classmethod
    def _no_padding(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("analyte code has leading or trailing spaces")
        return value

    def __str__(self) -> str:
        return f"{self.code} (analysis {self.analysis_id})"


class Quantity(Frozen):
    """A Decimal value with its unit. Never a float; never NaN or infinite (pydantic refuses those)."""

    value: Dec
    unit: str = Field(min_length=1, max_length=20)

    def __str__(self) -> str:
        return f"{self.value} {self.unit}"
