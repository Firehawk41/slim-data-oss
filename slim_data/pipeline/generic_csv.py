"""generic_csv/1 (DESIGN.md §9): one CSV file = one run, one row per reading.

UTF-8 (a byte-order mark is fine), any line ending, a header row. A cup rejected during the run and
measured again appears twice; the rejected row says so in `rejected` (with the reason in `note`).
Strict about columns: a missing required column or an unknown one (usually a typo) is a file error.
Values are read as text and turned into Decimals, so `12.40` keeps its digits; floats never appear.
"""

import csv
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import ClassVar

from slim_data.domain.enums import ReadingRole, SourceKind
from slim_data.domain.values import SampleRef
from slim_data.pipeline.reader import ParsedFile, ParsedRow, RowError, RunHeader

BLANK_ID = "BLANK"  # lab_sample_id of a method blank (D-64)
BATCH_IDS = {ReadingRole.BLANK: BLANK_ID, ReadingRole.CHECK: "CHECK", ReadingRole.CRM: "CRM"}  # D-87
RUN_COLUMNS = ("run_code", "instrument", "analyst", "analyzed_at")
REQUIRED = (
    "lab_sample_id",
    "analysis_code",
    "analyte",
    "role",
    "replicate",
    "value",
    "unit",
    "prep_batch",
    *RUN_COLUMNS,
)
OPTIONAL = (
    "mdl",
    "dilution_factor",
    "prep_method",
    "spike_added",
    "expected",
    "qc_material",
    "qc_lot",
    "rejected",
    "note",
)
_YES = {"yes", "y", "true", "1", "x", "rejected"}
_NO = {"", "no", "n", "false", "0"}
_WHOLE = re.compile(r"[0-9]+")


class _Bad(ValueError):
    def __init__(self, column: str, message: str) -> None:
        super().__init__(message)
        self.column = column


class GenericCsv:
    name: ClassVar[str] = "generic_csv"
    version: ClassVar[int] = 1
    source_kind: ClassVar[SourceKind] = SourceKind.CSV

    def parse(self, content: bytes) -> ParsedFile:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            return _file_error("not UTF-8 text")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        header = [name.strip() for name in reader.fieldnames or []]
        if not header:
            return _file_error("empty file: expected a header row")
        reader.fieldnames = header
        missing = [c for c in REQUIRED if c not in header]
        unknown = [c for c in header if c not in REQUIRED + OPTIONAL]
        duplicated = sorted({c for c in header if header.count(c) > 1})
        problems = [
            *(f"missing column {c}" for c in missing),
            *(f"unknown column {c!r}" for c in unknown),
            *(f"column {c} appears twice" for c in duplicated),
        ]
        if problems:
            return ParsedFile(run=None, errors=tuple(RowError(line=0, message=p) for p in problems))
        rows, errors, runs = [], [], set()
        for record in reader:
            line = reader.line_num
            if None in record or any(v is None for v in record.values()):
                errors.append(RowError(line=line, message="wrong number of fields"))
                continue
            cells = {k: v.strip() for k, v in record.items()}
            if not any(cells.values()):
                continue  # a blank line
            try:
                run, row = _run(cells), _row(line, cells)
            except _Bad as bad:
                errors.append(RowError(line=line, column=bad.column, message=str(bad)))
                continue
            runs.add(run)
            rows.append(row)
        if not rows and not errors:
            errors.append(RowError(line=0, message="no readings: the file has only a header"))
        if len(runs) > 1:
            errors.append(RowError(line=0, message="run columns differ between rows: one file is one run"))
        run = runs.pop() if len(runs) == 1 else None
        return ParsedFile(run=run, rows=tuple(rows), errors=tuple(errors))


def _file_error(message: str) -> ParsedFile:
    return ParsedFile(run=None, errors=(RowError(line=0, message=message),))


def _required(cells: dict, column: str) -> str:
    value = cells.get(column, "")
    if not value:
        raise _Bad(column, "is empty")
    return value


def _decimal(cells: dict, column: str, required: bool = True) -> Decimal | None:
    text = cells.get(column, "")
    if not text:
        if required:
            raise _Bad(column, "is empty")
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise _Bad(column, f"not a number: {text!r}") from None
    if not value.is_finite():
        raise _Bad(column, f"not a number: {text!r}")
    return value


def _run(cells: dict) -> RunHeader:
    text = _required(cells, "analyzed_at")
    try:
        analyzed_at = datetime.fromisoformat(text)
    except ValueError:
        raise _Bad("analyzed_at", f"not an ISO 8601 date-time: {text!r}") from None
    if analyzed_at.tzinfo is None:
        raise _Bad("analyzed_at", "needs a UTC offset, e.g. 2026-10-19T09:30:00-05:00")
    return RunHeader(
        run_code=_required(cells, "run_code"),
        instrument=_required(cells, "instrument"),
        analyst_id=_required(cells, "analyst"),
        analyzed_at=analyzed_at,
    )


def _row(line: int, cells: dict) -> ParsedRow:
    role_text = _required(cells, "role").upper()
    try:
        role = ReadingRole[role_text]
    except KeyError:
        raise _Bad("role", f"expected CUP, SPIKE, BLANK, CHECK or CRM, not {role_text!r}") from None
    sample_id = _required(cells, "lab_sample_id")
    if role.of_batch:
        if sample_id.upper() != BATCH_IDS[role]:
            raise _Bad("lab_sample_id", f"a {role.name} row's lab_sample_id is {BATCH_IDS[role]}")
        sample = None
    else:
        try:
            sample = SampleRef.parse(sample_id)
        except ValueError as error:
            raise _Bad("lab_sample_id", str(error)) from None
    replicate_text = _required(cells, "replicate")
    if not _WHOLE.fullmatch(replicate_text) or int(replicate_text) < 1:
        raise _Bad("replicate", f"expected a whole number from 1, not {replicate_text!r}")
    dilution = _decimal(cells, "dilution_factor", required=False)
    spike_added = _decimal(cells, "spike_added", required=False)
    if spike_added is not None and role != ReadingRole.SPIKE:
        raise _Bad("spike_added", "only a SPIKE row records the amount added")
    expected = _decimal(cells, "expected", required=role.is_qc)
    if not role.is_qc:
        for column in ("expected", "qc_material", "qc_lot"):
            if cells.get(column):
                raise _Bad(column, "only a CHECK or CRM row has one")
    rejected_text = cells.get("rejected", "").lower()
    if rejected_text not in _YES | _NO:
        raise _Bad("rejected", f"expected yes or no, not {rejected_text!r}")
    return ParsedRow(
        line=line,
        sample=sample,
        analysis_code=_required(cells, "analysis_code"),
        analyte_code=_required(cells, "analyte"),
        role=role,
        replicate=int(replicate_text),
        value=_decimal(cells, "value"),
        unit=_required(cells, "unit"),
        mdl=_decimal(cells, "mdl", required=False),
        dilution=Decimal(1) if dilution is None else dilution,
        prep_batch=_required(cells, "prep_batch"),
        prep_method=cells.get("prep_method") or None,
        spike_added=spike_added,
        expected=expected,
        qc_material=cells.get("qc_material") or None,
        qc_lot=cells.get("qc_lot") or None,
        rejected=rejected_text in _YES,
        note=cells.get("note") or None,
    )
