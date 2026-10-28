"""qtegra/1 (D-89): Thermo Qtegra's SpreadsheetML export of one LabBook = one run.

One sheet: four header rows (block, sweep, isotope with mode, unit), then one row per measurement in
run order. Blocks are found by their header names, never by column number, and every block the
reader doesn't use is ignored, so a fuller export reads the same.

* Values are `ExtCal.Average` and `ExtCal.RSD`: they respect the sweeps the analyst or Qtegra left
  out. A column whose `ExtCal.Average` unit is % is an internal standard: its value is the recovery,
  stored on every reading of the row (the lowest, if there are several).
* Rows by Sample Type: BLK is skipped; STD gives the calibration (r, R², LOD, BEC); QC with QC Action
  CCV is a CHECK reading, expected = `ExtCal.QC.ReferenceAmount`; UNKNOWN is identified by its
  Comment: `TR00012-01 A` (cup A = replicate 1) or `BLANK`. Anything else is listed as ignored.
* MDL = the run's `ExtCal.LOD`. Qtegra multiplies by the Dilution Factor; slim-data keeps the reading
  before dilution (D-59), so the value is divided by it and the factor stored.
* What Qtegra doesn't know (the prep batch, the analysis, who imports) comes in as reader options,
  chosen on the import screen; Qtegra's start times have no zone, so the lab's comes in too.

Pure: no database, no lookups. The file is XML from outside: a DOCTYPE (entities) is refused.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, tzinfo
from decimal import Decimal, InvalidOperation, localcontext
from typing import ClassVar
from xml.parsers import expat

from slim_data.domain.enums import ReadingRole, SourceKind
from slim_data.domain.values import SampleRef
from slim_data.pipeline.reader import ParsedCalibration, ParsedFile, ParsedRow, RowError, RunHeader

_SS = "urn:schemas-microsoft-com:office:spreadsheet"
_NS = {"ss": _SS}
_INDEX = f"{{{_SS}}}Index"
_ISOTOPE = re.compile(r"^(\d+)([A-Z][a-z]?) \((.+)\)$")  # "65Cu (CP-Off-None)"
_UNIT = re.compile(r"^Y \((.+)\)$")  # "Y (ppt)"
_CUP = re.compile(r"^(\S+)\s+([A-Z])$")  # "TR00012-01 A"
_FRACTION = re.compile(r"(\.\d{6})\d+")  # Qtegra writes 7 decimals of a second; Python reads 6
_NO_COMMENT = {"", "<comment>"}

AVERAGE, RSD = "ExtCal.Average", "ExtCal.RSD"
QC_REFERENCE = "ExtCal.QC.ReferenceAmount"
CALIBRATION = {
    "ExtCal.CorrelationCoefficient": "correlation",
    "ExtCal.CoefficientOfDetermination": "determination",
    "ExtCal.LOD": "lod",
    "ExtCal.BEC": "bec",
}
ROW_FIELDS = ("Sample Type", "Dilution Factor", "QC Action", "Standard")


class _Bad(ValueError):
    def __init__(self, column: str, message: str) -> None:
        super().__init__(message)
        self.column = column


@dataclass(frozen=True)
class _Columns:
    instrument: str
    comment: int
    start: int
    fields: dict  # Sample Type, ... -> column
    average: dict  # element -> (column, unit)
    rsd: dict  # element -> column
    istd: tuple  # columns of the internal standards' recoveries
    qc_reference: dict  # element -> column
    calibration: dict  # (element, attribute) -> column


@dataclass(frozen=True)
class Qtegra:
    """A reader instance carries what the file doesn't: the import screen's choices (D-89)."""

    name: ClassVar[str] = "qtegra"
    version: ClassVar[int] = 1
    source_kind: ClassVar[SourceKind] = SourceKind.INSTRUMENT

    prep_batch: str
    analysis_code: str
    analyst_id: str  # whoever imports: the file's user name is the instrument PC's shared login
    timezone: tzinfo  # the lab's: Qtegra's start times have none
    prep_method: str | None = None
    run_code: str | None = None  # default: instrument and first start time

    def parse(self, content: bytes) -> ParsedFile:
        try:
            root = _xml(content)
        except _Doctype:
            return _file_error("a DOCTYPE in the file: not a Qtegra export")
        except ET.ParseError as error:
            return _file_error(f"not XML: {error}")
        table = root.find("ss:Worksheet/ss:Table", _NS)
        if table is None:
            return _file_error("no worksheet: not a SpreadsheetML export")
        grid = _grid(table)
        header = [grid.get(i, {}) for i in (1, 2, 3, 4)]
        try:
            columns = _columns(header)
        except _Bad as bad:
            return _file_error(str(bad))
        body = sorted((i, cells) for i, cells in grid.items() if i > 4)
        errors: list[RowError] = []
        ignored: list[RowError] = []
        calibration = _calibration(body, columns)
        lods = {element: c.lod for element, c in calibration.items()}
        rows: list[ParsedRow] = []
        counts = {ReadingRole.BLANK: 0, ReadingRole.CHECK: 0}
        starts = []
        for line, cells in body:
            try:
                parsed = self._row(line, cells, columns, lods, counts, ignored)
                start = _start(cells, columns) if parsed else None
            except _Bad as bad:
                errors.append(RowError(line=line, column=bad.column, message=str(bad)))
                continue
            if parsed:
                rows.extend(parsed)
                starts.append(start)
        if not rows and not errors:
            errors.append(RowError(line=0, message="no readings: no sample, blank or check standard rows"))
        run = None
        if starts and not any(s is None for s in starts):
            analyzed_at = min(starts).replace(tzinfo=self.timezone)
            run = RunHeader(
                run_code=self.run_code or f"{columns.instrument}-{analyzed_at:%Y%m%d-%H%M}"[:50],
                instrument=columns.instrument,
                analyst_id=self.analyst_id,
                analyzed_at=analyzed_at,
            )
        elif starts:
            errors.append(RowError(line=0, column="StartTime", message="a row has no start time"))
        return ParsedFile(
            run=run,
            rows=tuple(rows),
            errors=tuple(errors),
            calibrations=tuple(calibration.values()),
            ignored=tuple(ignored),
        )

    def _row(self, line, cells, columns, lods, counts, ignored) -> list[ParsedRow]:
        kind = (_text(cells, columns.fields["Sample Type"]) or "").upper()
        if kind in ("BLK", "STD"):
            return []  # the calibration blank and standards: the calibration, not readings
        averages = {e: _number(cells, c, "ExtCal.Average") for e, (c, _) in columns.average.items()}
        if all(v is None for v in averages.values()):
            ignored.append(RowError(line=line, message="no values (an aborted measurement?)"))
            return []
        sample, role, replicate, expected, material = None, None, 1, {}, None
        if kind == "QC":
            action = _text(cells, columns.fields.get("QC Action")) or ""
            if action.upper() != "CCV":
                message = f"a QC row with action {action!r}"
                ignored.append(RowError(line=line, column="QC Action", message=message))
                return []
            role, material = ReadingRole.CHECK, _text(cells, columns.fields.get("Standard"))
            expected = {e: _number(cells, c, QC_REFERENCE) for e, c in columns.qc_reference.items()}
        elif kind == "UNKNOWN":
            comment = (_text(cells, columns.comment) or "").strip()
            if comment.lower() in _NO_COMMENT:
                ignored.append(RowError(line=line, column="Comment", message="no sample ID in the comment"))
                return []
            if comment.upper() == "BLANK":
                role = ReadingRole.BLANK
            else:
                role, (sample, replicate) = ReadingRole.CUP, _cup(comment)
        else:
            message = f"sample type {kind or 'missing'}"
            ignored.append(RowError(line=line, column="Sample Type", message=message))
            return []
        if role.of_batch:
            counts[role] += 1
            replicate = counts[role]  # each blank and check standard of the run is judged
        dilution = _number(cells, columns.fields.get("Dilution Factor"), "Dilution Factor")
        dilution = Decimal(1) if dilution is None else dilution
        if dilution <= 0:  # below 1 is fine: an evaporation concentrates (D-93)
            raise _Bad("Dilution Factor", f"must be positive, not {dilution}")
        rsds = {e: _number(cells, c, "ExtCal.RSD") for e, c in columns.rsd.items()}
        recoveries = [r for r in (_number(cells, c, "ExtCal.Average") for c in columns.istd) if r is not None]
        istd = min(recoveries) if recoveries else None
        rows = []
        for element, value in averages.items():
            if value is None:
                continue
            if role.is_qc and expected.get(element) is None:
                raise _Bad(QC_REFERENCE, f"no reference amount for {element}: export Quality Control")
            with localcontext() as context:
                context.prec = 34
                before_dilution = value / dilution
            rows.append(
                ParsedRow(
                    line=line,
                    sample=sample,
                    analysis_code=self.analysis_code,
                    analyte_code=element,
                    role=role,
                    replicate=replicate,
                    value=before_dilution,
                    unit=columns.average[element][1],
                    mdl=lods.get(element),
                    dilution=dilution,
                    prep_batch=self.prep_batch,
                    prep_method=self.prep_method,
                    spike_added=None,
                    expected=expected.get(element),
                    qc_material=material,
                    rsd=rsds.get(element),
                    istd_recovery=istd,
                )
            )
        return rows


class _Doctype(Exception):
    pass


def _xml(content: bytes) -> ET.Element:
    """Refuses a DOCTYPE with expat itself before parsing, so no encoding (UTF-16, say) slips one
    past a byte check: no DTD, no entities. Malformed XML is left for ElementTree to report."""

    def refuse(*_):
        raise _Doctype

    check = expat.ParserCreate()
    check.StartDoctypeDeclHandler = refuse
    try:
        check.Parse(content, True)
    except expat.ExpatError:
        pass
    return ET.fromstring(content)


def _file_error(message: str) -> ParsedFile:
    return ParsedFile(run=None, errors=(RowError(line=0, message=message),))


def _grid(table) -> dict[int, dict[int, str | None]]:
    """Row index -> column index -> the cell's text. SpreadsheetML may leave out an `Index`: then a
    row or cell follows the one before it."""
    grid, row_index = {}, 0
    for row in table.findall("ss:Row", _NS):
        row_index = int(row.get(_INDEX, row_index + 1))
        cells, column = {}, 0
        for cell in row.findall("ss:Cell", _NS):
            column = int(cell.get(_INDEX, column + 1))
            data = cell.find("ss:Data", _NS)
            cells[column] = None if data is None else data.text
        grid[row_index] = cells
    return grid


def _columns(header: list[dict]) -> _Columns:
    block, sub, name, unit = header
    instrument = comment = start = None
    fields, average, rsd, istd, qc, calibration = {}, {}, {}, [], {}, {}
    for column, title in block.items():
        detail = sub.get(column)
        if title == "StartTime":
            start = column
        elif detail == "Comment":
            instrument, comment = title, column
        elif detail == "ExtCal" and name.get(column) in ROW_FIELDS:
            fields[name[column]] = column
        elif title in (AVERAGE, RSD, QC_REFERENCE, *CALIBRATION) and (detail == "0" or title == QC_REFERENCE):
            element = _element(name.get(column))
            measured_in = _unit(unit.get(column))
            if title == AVERAGE and measured_in == "%":
                istd.append(column)
            elif title == AVERAGE:
                if element in average:
                    raise _Bad("", f"two isotopes of {element}: export one")
                average[element] = (column, measured_in)
            elif title == RSD:
                rsd[element] = column
            elif title == QC_REFERENCE:
                qc[element] = column
            else:
                calibration[(element, CALIBRATION[title])] = column
    missing = [
        what
        for what, found in (
            ("Comment (Sample list)", comment),
            ("Start Time (Sample list)", start),
            ("Sample Type (Sample list)", fields.get("Sample Type")),
            ("ExtCal.Average (Concentrations)", average),
        )
        if not found
    ]
    if missing:
        raise _Bad("", "not in the export: " + ", ".join(missing) + " (D-89's scheme)")
    return _Columns(instrument, comment, start, fields, average, rsd, tuple(istd), qc, calibration)


def _calibration(body, columns: _Columns) -> dict[str, ParsedCalibration]:
    """From the first standard row that has each figure: Qtegra repeats the run's calibration on
    every standard row. An internal standard's zeros never get here (it isn't an analyte)."""
    found: dict[str, dict] = {}
    for _, cells in body:
        for (element, attribute), column in columns.calibration.items():
            if element not in columns.average:
                continue
            value = _number(cells, column, attribute)
            if value is not None:
                found.setdefault(element, {}).setdefault(attribute, value)
    return {
        element: ParsedCalibration(analyte_code=element, unit=columns.average[element][1], **figures)
        for element, figures in found.items()
    }


def _start(cells, columns: _Columns) -> datetime | None:
    text = _text(cells, columns.start)
    if not text:
        return None
    try:
        return datetime.fromisoformat(_FRACTION.sub(r"\1", text))
    except ValueError:
        raise _Bad("StartTime", f"not a date-time: {text!r}") from None


def _cup(comment: str) -> tuple[SampleRef, int]:
    match = _CUP.match(comment)
    if not match:
        raise _Bad("Comment", f"expected a sample ID and cup (e.g. TR00012-01 A) or BLANK, not {comment!r}")
    try:
        sample = SampleRef.parse(match.group(1))
    except ValueError as error:
        raise _Bad("Comment", str(error)) from None
    return sample, ord(match.group(2)) - ord("A") + 1


def _element(name: str | None) -> str:
    match = _ISOTOPE.match(name or "")
    if not match:
        raise _Bad("", f"not an isotope heading: {name!r}")
    return match.group(2)


def _unit(text: str | None) -> str:
    match = _UNIT.match(text or "")
    return match.group(1) if match else (text or "")


def _text(cells: dict, column: int | None) -> str | None:
    return None if column is None else cells.get(column)


def _number(cells: dict, column: int | None, what: str) -> Decimal | None:
    text = _text(cells, column)
    if text is None or not text.strip():
        return None
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        raise _Bad(what, f"not a number: {text!r}") from None
    if not value.is_finite():
        raise _Bad(what, f"not a number: {text!r}")
    return value
