"""The reader contract (D-25): a plugin turns one instrument file into typed rows, or says exactly
which rows and columns are wrong. Pure: no database, no lookups. What the rows *mean* (which batch,
which analysis, who the analyst is) is decided later, by the import plan.
"""

from datetime import datetime
from decimal import Decimal
from typing import ClassVar, Protocol

from pydantic import Field

from slim_data.domain.base import Frozen
from slim_data.domain.enums import ReadingRole
from slim_data.domain.values import SampleRef


class RowError(Frozen):
    line: int  # file line, the header being line 1; 0 = the file as a whole
    column: str = ""
    message: str

    def __str__(self) -> str:
        where = "file" if self.line == 0 else f"line {self.line}"
        return f"{where}{f', {self.column}' if self.column else ''}: {self.message}"


class RunHeader(Frozen):
    """The run columns, which must be the same on every row (one file = one run)."""

    run_code: str
    instrument: str
    analyst_id: str
    analyzed_at: datetime


class ParsedRow(Frozen):
    line: int
    sample: SampleRef | None  # None for a method blank, check standard or CRM (D-64, D-87)
    analysis_code: str
    analyte_code: str
    role: ReadingRole
    replicate: int
    value: Decimal
    unit: str
    mdl: Decimal | None
    dilution: Decimal
    prep_batch: str
    prep_method: str | None
    spike_added: Decimal | None
    expected: Decimal | None = None  # a CHECK or CRM row's expected value (D-87)
    qc_material: str | None = None
    qc_lot: str | None = None
    rejected: bool = False  # rejected in the run (e.g. by the technician), usually then rerun (D-66)
    note: str | None = None  # why, recorded as the exclusion's reason


class ParsedFile(Frozen):
    run: RunHeader | None
    rows: tuple[ParsedRow, ...] = ()
    errors: tuple[RowError, ...] = Field(default=())


class ResultReader(Protocol):
    name: ClassVar[str]  # e.g. "generic_csv"
    version: ClassVar[int]  # a changed reader is a new version; old ones stay (DESIGN.md §12.3)

    def parse(self, content: bytes) -> ParsedFile: ...


def reader_key(reader: ResultReader) -> str:
    return f"{reader.name}/{reader.version}"
