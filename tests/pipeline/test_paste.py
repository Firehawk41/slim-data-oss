"""paste/1 (D-88): generic_csv/1's columns from a table copied out of Excel."""

from decimal import Decimal
from pathlib import Path

from slim_data.domain.enums import SourceKind
from slim_data.pipeline.generic_csv import GenericCsv
from slim_data.pipeline.paste import Paste
from slim_data.pipeline.reader import reader_key

GOOD = (Path(__file__).parent / "fixtures" / "good.csv").read_text()


def as_pasted(csv_text: str) -> bytes:
    """What Excel puts on the clipboard: tabs between cells, CRLF line ends."""
    return "\r\n".join(line.replace(",", "\t") for line in csv_text.splitlines()).encode()


def test_a_paste_reads_like_the_csv_it_came_from():
    pasted, from_csv = Paste().parse(as_pasted(GOOD)), GenericCsv().parse(GOOD.encode())
    assert pasted.errors == () and pasted.run == from_csv.run and pasted.rows == from_csv.rows
    assert reader_key(Paste()) == "paste/1" and Paste.source_kind == SourceKind.PASTE


def test_a_comma_inside_a_pasted_cell_is_text_not_a_separator():
    header, first, *_ = GOOD.splitlines()
    note = first.split(",")
    note[header.split(",").index("note")] = "bubble, rerun"
    parsed = Paste().parse(as_pasted(header) + b"\r\n" + "\t".join(note).encode())
    assert parsed.errors == () and parsed.rows[0].note == "bubble, rerun"


def test_excel_quotes_a_cell_with_a_tab():
    header, first, *_ = GOOD.splitlines()
    cells = first.split(",")
    cells[header.split(",").index("note")] = '"tab\there"'
    parsed = Paste().parse(as_pasted(header) + b"\r\n" + "\t".join(cells).encode())
    assert parsed.errors == () and parsed.rows[0].note == "tab\there"


def test_a_comma_separated_paste_is_refused_with_the_column_problem():
    parsed = Paste().parse(GOOD.encode())
    assert parsed.run is None and "missing column" in parsed.errors[0].message


def test_values_keep_their_digits():
    parsed = Paste().parse(as_pasted(GOOD))
    assert parsed.rows[0].value == Decimal("10.00") and str(parsed.rows[0].value) == "10.00"
