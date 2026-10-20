"""Shared building blocks for domain objects: the frozen base model and a float-free Decimal type."""

from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict


class Frozen(BaseModel):
    """Immutable, strict-shape base for every domain object."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def _reject_float(value: Any) -> Any:
    # A float has already lost digits (0.1 + 0.2 != 0.3); values must arrive as str, int or Decimal.
    if isinstance(value, float):
        raise ValueError("use a string or Decimal, not a float: floats lose digits")
    return value


Dec = Annotated[Decimal, BeforeValidator(_reject_float)]
"""A Decimal that refuses float input. Every measured or reported number uses it."""
