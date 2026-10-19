"""Decimal arithmetic for the engine: one fixed context, so a replay computes exactly the same digits."""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)

ENGINE_CONTEXT = Context(
    prec=28, rounding=ROUND_HALF_EVEN, traps=[InvalidOperation, DivisionByZero, Overflow]
)


@contextmanager
def engine_arithmetic() -> Iterator[None]:
    """Every evaluation runs inside this context, whatever the caller's global decimal settings."""
    with localcontext(ENGINE_CONTEXT):
        yield


def rpd(a: Decimal, b: Decimal) -> Decimal:
    """Relative percent difference as a fraction: |a − b| / mean. Zero when both are zero."""
    mean = (a + b) / 2
    if mean == 0:
        return Decimal(0)
    return abs(a - b) / abs(mean)


def median(values: Iterable[Decimal]) -> Decimal:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("median of nothing")
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def text(value: Decimal | None) -> str | None:
    """How a Decimal appears in a trace: its exact digits as a string (JSON has no Decimal)."""
    return None if value is None else str(value)
