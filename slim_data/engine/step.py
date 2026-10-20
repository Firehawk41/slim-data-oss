"""The step protocol (D-13, D-17).

A step is a named, versioned procedure: `agreement@1`. A published ruleset pins exact versions, and a
version is never edited or removed once released; a fix is a new version. That is what makes replay
possible: the same readings, ruleset and step versions always give the same trace.
"""

from typing import ClassVar

from pydantic import BaseModel

from slim_data.domain.base import Frozen
from slim_data.engine.model import BatchState, Cell, EmittedFlag


class NoParams(Frozen):
    pass


class Step:
    name: ClassVar[str]
    version: ClassVar[int]
    Params: ClassVar[type[BaseModel]] = NoParams
    flag_codes: ClassVar[frozenset[str]] = frozenset()  # every code it may emit; the ruleset maps each
    requires: ClassVar[tuple[str, ...]] = ()  # step names that must run earlier

    @classmethod
    def key(cls) -> str:
        return f"{cls.name}@{cls.version}"

    def warnings(self, params: BaseModel) -> list[str]:
        """Things worth a warning when a ruleset is published with these params (e.g. D-30)."""
        return []

    def run(self, state: BatchState, params: BaseModel) -> None:
        raise NotImplementedError

    def flag(self, cell: Cell, code: str, message: str = "") -> None:
        if code not in self.flag_codes:
            raise AssertionError(f"{self.key()} emitted undeclared flag {code}")
        cell.flags.append(EmittedFlag(code=code, step=self.key(), message=message))

    def trace(self, cell: Cell, **data) -> None:
        cell.trace.append({"step": self.key(), **data})
